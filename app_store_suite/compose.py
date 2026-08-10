from __future__ import annotations

import colorsys
import hashlib
import io
import math
import random
import re
import shutil
from collections import Counter
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from . import devices as devices_mod
from . import style_choices, titles_store
from .config import DeviceConfig, StudioConfig, StyleConfig
from .frames import fetch as frames_fetch

_FONTS_DIR = Path(__file__).parent / "fonts"
_MARGIN_RATIO = 0.08  # side margin as a fraction of canvas width
# Tilted devices need a much smaller side margin than upright ones: fitting the
# rotated (larger) bounding box inside the same margin as an upright device forces it
# noticeably smaller, and the diagonal corners taper away from the canvas edge anyway.
_TILT_MARGIN_RATIO = 0.02
_TOP_PADDING_RATIO = 0.065  # space above the title text, as a fraction of canvas height
_TEXT_DEVICE_GAP_RATIO = 0.04  # space between the subtitle/title block and the device, ditto

# Bundled fallback with broad script coverage (Greek, Cyrillic, etc.), used
# whenever the configured brand font (e.g. Poppins, which is Latin-only) is
# missing glyphs for the text being rendered.
_FALLBACK_FONTS = {"bold": "NotoSans-Bold.ttf", "regular": "NotoSans-Regular.ttf"}

_cmap_cache: dict[str, set[int]] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    # layout_engine=BASIC opts out of Pillow's raqm-based complex text shaping.
    # We don't need it (no ligatures/RTL/reordering in any script this project
    # renders) and it has a real bug: a bidi/script-run mismatch right at a
    # Latin-punctuation-then-Greek boundary (e.g. ", χάρτης") can misplace one
    # cluster's glyph origin, rendering as a spurious mid-word gap. BASIC just
    # advances glyph-by-glyph per character and doesn't hit this.
    return ImageFont.truetype(str(_FONTS_DIR / name), size, layout_engine=ImageFont.Layout.BASIC)


def _covers(font_name: str, text: str) -> bool:
    if font_name not in _cmap_cache:
        _cmap_cache[font_name] = set(TTFont(str(_FONTS_DIR / font_name)).getBestCmap())
    cmap = _cmap_cache[font_name]
    return all(ord(c) in cmap for c in text if not c.isspace())


def _font_for_text(brand_font_name: str, weight: str, size: int, text: str) -> ImageFont.FreeTypeFont:
    """Picks the configured brand font if it covers `text`'s characters, otherwise
    falls back to a bundled font with broader script coverage."""
    name = brand_font_name if _covers(brand_font_name, text) else _FALLBACK_FONTS[weight]
    return _font(name, size)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def _readable_text_color(
    bg_rgb: tuple[int, int, int], preferred_rgb: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Keeps the configured title_color if it already contrasts against this
    background; otherwise falls back to white-on-dark or near-black-on-light so text
    stays legible regardless of background_mode/decoration."""
    if abs(_luminance(bg_rgb) - _luminance(preferred_rgb)) > 110:
        return preferred_rgb
    return (255, 255, 255) if _luminance(bg_rgb) < 128 else (26, 26, 26)


def _parse_offset(offset: str) -> tuple[int, int]:
    match = re.match(r"\+(-?\d+)\+(-?\d+)", offset)
    if not match:
        raise ValueError(f"Unrecognized offset format: {offset!r}")
    return int(match.group(1)), int(match.group(2))


_silhouette_mask_cache: dict[str, Image.Image] = {}


def _silhouette_mask(frame_path: Path, frame: Image.Image) -> Image.Image | None:
    """Frame PNGs are drawn on a canvas larger than the phone body (room for drop
    shadows etc.), fully transparent outside it. Compositing the raw screenshot onto
    that canvas and laying the frame on top only hides the screenshot behind the
    bezel's *opaque* pixels — it doesn't hide screenshot pixels that spill past the
    bezel into that outer transparent padding (invisible when upright against a
    matching flat background, but visible once the frame is rotated). This floods
    that outer transparent region (from the canvas corner) to build a mask that clips
    it away, while leaving the screen cutout itself untouched. Returns None if the
    corner isn't transparent (unexpected asset shape) rather than risk clipping wrong."""
    key = str(frame_path)
    if key in _silhouette_mask_cache:
        return _silhouette_mask_cache[key]

    alpha = frame.split()[3]
    if alpha.getpixel((0, 0)) != 0:
        _silhouette_mask_cache[key] = None
        return None

    binary = alpha.point(lambda a: 255 if a == 0 else 0).convert("RGB")
    ImageDraw.floodfill(binary, (0, 0), (128, 0, 0), thresh=10)
    mask = binary.split()[0].point(lambda r: 0 if r == 128 else 255)
    _silhouette_mask_cache[key] = mask
    return mask


def _framed_device_image(raw: Image.Image, device: DeviceConfig) -> Image.Image:
    spec = devices_mod.resolve_frame(device)
    if spec.frame_file and spec.offset_key:
        offsets = frames_fetch.load_offsets()
        entry = offsets.get(spec.offset_key)
        if entry:
            frame_path = frames_fetch.get_frame_path(spec.frame_file)
            frame = Image.open(frame_path).convert("RGBA")
            x, y = _parse_offset(entry["offset"])
            target_w = entry["width"]
            scale = target_w / raw.width
            resized = raw.resize((target_w, round(raw.height * scale)), Image.LANCZOS)
            canvas = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            canvas.paste(resized, (x, y))
            canvas.alpha_composite(frame)

            mask = _silhouette_mask(frame_path, frame)
            if mask is not None:
                r, g, b, a = canvas.split()
                canvas.putalpha(ImageChops.darker(a, mask))
            return canvas

    return _procedural_frame(raw)


def _procedural_frame(raw: Image.Image) -> Image.Image:
    """Fallback when no matching frameit-frames bezel exists: rounded corners + soft shadow."""
    pad = round(raw.width * 0.04)
    radius = round(raw.width * 0.08)
    canvas = Image.new("RGBA", (raw.width + pad * 2, raw.height + pad * 2), (0, 0, 0, 0))

    mask = Image.new("L", raw.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, raw.width, raw.height], radius=radius, fill=255)
    rounded = Image.new("RGBA", raw.size, (0, 0, 0, 0))
    rounded.paste(raw.convert("RGBA"), (0, 0), mask)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [pad, pad + round(pad * 0.4), pad + raw.width, pad + round(pad * 0.4) + raw.height],
        radius=radius,
        fill=(0, 0, 0, 70),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(pad * 0.3))

    canvas.alpha_composite(shadow)
    canvas.alpha_composite(rounded, (pad, pad))
    return canvas


def _background_color_for(
    raw: Image.Image, style_background_hex: str, mode: str
) -> tuple[int, int, int]:
    """"solid" always uses the configured color. "auto" samples the raw screenshot's
    own edge pixels (the app's chrome, not whatever photo/content sits mid-screen) and
    lightens the result toward white if it's too dark for the (dark) title text."""
    fallback = _hex_to_rgb(style_background_hex)
    if mode != "auto":
        return fallback

    img = raw.convert("RGB")
    w, h = img.size
    strip = max(2, round(min(w, h) * 0.02))
    edge_pixels = (
        list(img.crop((0, 0, w, strip)).getdata())
        + list(img.crop((0, h - strip, w, h)).getdata())
        + list(img.crop((0, 0, strip, h)).getdata())
        + list(img.crop((w - strip, 0, w, h)).getdata())
    )
    if not edge_pixels:
        return fallback

    color, _ = Counter(edge_pixels).most_common(1)[0]
    r, g, b = color
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    if luminance < 180:
        blend = 0.6
        r = round(r + (255 - r) * blend)
        g = round(g + (255 - g) * blend)
        b = round(b + (255 - b) * blend)
    return (r, g, b)


def _accent_color_for(raw: Image.Image) -> tuple[int, int, int] | None:
    """Picks a vivid, representative color from the screenshot's own content (not its
    white/gray UI chrome) — e.g. the sky or a photo's dominant hue — for auto-deriving
    gradient/decoration colors per shot. Returns None if nothing sufficiently colorful
    is found (e.g. an all-white/gray screen), so callers can fall back sensibly."""
    img = raw.convert("RGB").resize((64, 64))
    buckets: Counter[tuple[int, int, int]] = Counter()
    for r, g, b in img.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.28 or v < 0.25 or v > 0.97:
            continue  # skip near-gray, near-black, near-white pixels (typical UI chrome)
        buckets[(r // 24 * 24, g // 24 * 24, b // 24 * 24)] += 1
    if not buckets:
        return None
    return buckets.most_common(1)[0][0]


def _auto_gradient_color2(raw: Image.Image, base_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    accent = _accent_color_for(raw)
    if accent is None:
        return tuple(min(255, c + 40) for c in base_rgb)
    return _lerp_color(accent, (255, 255, 255), 0.55)  # pastel-ify for a soft gradient


def _auto_decoration_color(raw: Image.Image, fallback_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    accent = _accent_color_for(raw)
    return accent if accent is not None else fallback_rgb


def _seed_for(shot_id: str) -> int:
    """Deterministic per-shot seed so decoration/tilt choices stay stable across
    re-composing the same shot (and consistent across devices/languages for it)."""
    return int(hashlib.md5(shot_id.encode()).hexdigest()[:8], 16)


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _gradient_background(w: int, h: int, color1: tuple[int, int, int], color2: tuple[int, int, int]) -> Image.Image:
    column = Image.new("RGB", (1, h))
    for y in range(h):
        column.putpixel((0, y), _lerp_color(color1, color2, y / max(h - 1, 1)))
    return column.resize((w, h), Image.BILINEAR)


def _build_background(canvas_w: int, canvas_h: int, raw: Image.Image, style: StyleConfig) -> Image.Image:
    if style.background_mode == "gradient":
        color1 = _hex_to_rgb(style.background_color)
        color2 = (
            _hex_to_rgb(style.gradient_color2)
            if style.gradient_color2
            else _auto_gradient_color2(raw, color1)
        )
        return _gradient_background(canvas_w, canvas_h, color1, color2)
    bg_color = _background_color_for(raw, style.background_color, style.background_mode)
    return Image.new("RGB", (canvas_w, canvas_h), bg_color)


def _apply_overlay(canvas: Image.Image, overlay: Image.Image) -> None:
    """Alpha-composites an RGBA overlay onto an RGB canvas in place."""
    composited = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    canvas.paste(composited, (0, 0))


def _draw_shape_decorations(canvas: Image.Image, style: StyleConfig, shot_id: str, raw: Image.Image) -> None:
    """Soft, low-opacity blurred circles behind the device — no extra dependency."""
    rng = random.Random(_seed_for(shot_id))
    w, h = canvas.size
    color = (
        _hex_to_rgb(style.decoration_color)
        if style.decoration_color
        else _auto_decoration_color(raw, _hex_to_rgb(style.title_color))
    )

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(3):
        radius = rng.uniform(0.18, 0.32) * min(w, h)
        cx = rng.uniform(0.0, 1.0) * w
        cy = rng.uniform(0.0, 1.0) * h
        alpha = rng.randint(14, 26)
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(*color, alpha))
    overlay = overlay.filter(ImageFilter.GaussianBlur(min(w, h) * 0.04))
    _apply_overlay(canvas, overlay)


def _draw_svg_decoration(canvas: Image.Image, style: StyleConfig, shot_id: str, raw: Image.Image) -> None:
    """Rasterizes one .svg from decoration_svg_dir (picked deterministically per shot),
    tinted to decoration_color (or an accent color auto-sampled from this shot's own
    screenshot if unset — the source file's own fill is always ignored) and placed in a
    top corner behind the device, alternating corner and rotation per shot for variety."""
    if not style.decoration_svg_dir or not style.decoration_svg_dir.is_dir():
        return
    files = sorted(style.decoration_svg_dir.glob("*.svg"))
    if not files:
        return

    import cairosvg  # deferred: only needed when decoration_svg is actually configured

    seed = _seed_for(shot_id)
    svg_path = files[seed % len(files)]
    w, h = canvas.size
    target_h = round(h * 0.46)
    png_bytes = cairosvg.svg2png(url=str(svg_path), output_height=target_h)
    decoration = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    color = (
        _hex_to_rgb(style.decoration_color)
        if style.decoration_color
        else _auto_decoration_color(raw, _hex_to_rgb(style.title_color))
    )
    tinted = Image.new("RGBA", decoration.size, (*color, 0))
    tinted.putalpha(decoration.split()[3].point(lambda a: round(a * 0.30)))
    angle = 14 if seed % 2 == 0 else -12
    tinted = tinted.rotate(angle, expand=True, resample=Image.BICUBIC)

    margin = round(w * 0.04)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    if (seed // 2) % 2 == 0:
        px = w - tinted.width - margin
    else:
        px = margin - round(tinted.width * 0.2)
    py = margin - round(h * 0.02)
    overlay.paste(tinted, (px, py), tinted)
    _apply_overlay(canvas, overlay)


def _apply_decoration(canvas: Image.Image, style: StyleConfig, shot_id: str, raw: Image.Image) -> None:
    if style.decoration == "shapes":
        _draw_shape_decorations(canvas, style, shot_id, raw)
    elif style.decoration == "svg":
        _draw_svg_decoration(canvas, style, shot_id, raw)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def render_shot(
    cfg: StudioConfig,
    lang: str,
    device_key: str,
    device: DeviceConfig,
    shot_id: str,
    title: str,
    subtitle: str,
    raw_path: Path,
    style: StyleConfig | None = None,
    dest_override: Path | None = None,
) -> Path:
    """`style` defaults to `cfg.style`; pass an override (e.g. from style_choices or
    a style_variants preset) to render this one shot differently. `dest_override` is
    the file to write — required; compose_all points it directly at the real fastlane
    screenshots/images path, style-preview points it at a scratch comparison location."""
    if dest_override is None:
        raise ValueError("render_shot requires dest_override — there is no default output path")
    style = style or cfg.style
    canvas_w, canvas_h = devices_mod.store_resolution(device)
    raw = Image.open(raw_path)

    canvas = _build_background(canvas_w, canvas_h, raw, style)
    _apply_decoration(canvas, style, shot_id, raw)

    margin = round(canvas_w * _MARGIN_RATIO)
    top_padding = round(canvas_h * _TOP_PADDING_RATIO)

    # Sampled after background+decoration, before any text is drawn, so the color
    # picked reflects what text will actually sit on. The exact wrapped text height
    # isn't known yet, so this just samples a representative top band.
    sample_h = round(canvas_h * 0.2)
    title_band = ImageStat.Stat(canvas.crop((0, 0, canvas_w, sample_h))).mean[:3]
    title_color = _readable_text_color(tuple(round(c) for c in title_band), _hex_to_rgb(style.title_color))

    draw = ImageDraw.Draw(canvas)

    text_max_width = canvas_w - margin * 2

    title_font = _font_for_text(style.font_bold, "bold", round(canvas_w * 0.062), title)
    lines = _wrap_text(draw, title, title_font, text_max_width)
    line_height = title_font.size + round(title_font.size * 0.3)
    text_block_h = line_height * len(lines)

    sub_lines: list[str] = []
    sub_font: ImageFont.FreeTypeFont | None = None
    sub_line_height = 0
    sub_gap = 0
    if subtitle:
        sub_font = _font_for_text(style.font_regular, "regular", round(canvas_w * 0.032), subtitle)
        sub_lines = _wrap_text(draw, subtitle, sub_font, text_max_width)
        sub_line_height = sub_font.size + round(sub_font.size * 0.3)
        sub_gap = round(sub_font.size * 0.5)

    total_text_h = text_block_h + (sub_gap + sub_line_height * len(sub_lines) if sub_lines else 0)
    text_top = top_padding

    for i, line in enumerate(lines):
        w = draw.textlength(line, font=title_font)
        draw.text(
            ((canvas_w - w) / 2, text_top + i * line_height),
            line,
            font=title_font,
            fill=title_color,
        )

    if sub_lines:
        sub_top = text_top + text_block_h + sub_gap
        for i, line in enumerate(sub_lines):
            w = draw.textlength(line, font=sub_font)
            draw.text(
                ((canvas_w - w) / 2, sub_top + i * sub_line_height),
                line,
                font=sub_font,
                fill=title_color,
            )

    content_top = text_top + total_text_h + round(canvas_h * _TEXT_DEVICE_GAP_RATIO)

    framed = _framed_device_image(raw, device)

    device_area_w = canvas_w - margin * 2
    device_area_h = canvas_h - content_top - margin

    if style.layout == "tilted":
        # Scale directly against the *rotated* bounding box, not the upright one then
        # shrunk again — fitting upright first and re-shrinking after rotation wastes
        # space (the diagonal bounding box is bigger), leaving the device visibly
        # smaller/more surrounded by whitespace than the other layouts. Also use a much
        # smaller side margin — see _TILT_MARGIN_RATIO.
        device_area_w = canvas_w - round(canvas_w * _TILT_MARGIN_RATIO) * 2
        direction = 1 if _seed_for(shot_id) % 2 == 0 else -1
        angle = math.radians(style.tilt_degrees)
        cos_a, sin_a = abs(math.cos(angle)), abs(math.sin(angle))
        bbox_w = framed.width * cos_a + framed.height * sin_a
        bbox_h = framed.width * sin_a + framed.height * cos_a
        scale = min(device_area_w / bbox_w, device_area_h / bbox_h)
        framed_resized = framed.resize(
            (round(framed.width * scale), round(framed.height * scale)), Image.LANCZOS
        )
        framed_resized = framed_resized.rotate(
            style.tilt_degrees * direction, expand=True, resample=Image.BICUBIC
        )
    else:
        scale = min(device_area_w / framed.width, device_area_h / framed.height)
        framed_resized = framed.resize(
            (round(framed.width * scale), round(framed.height * scale)), Image.LANCZOS
        )

    paste_x = round((canvas_w - framed_resized.width) / 2)
    paste_y = canvas_h - margin - framed_resized.height
    canvas.paste(framed_resized, (paste_x, paste_y), framed_resized)

    dest = dest_override
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest


def _android_categories(device_key: str) -> list[str]:
    """Devices whose config key contains "tablet" go to both sevenInchScreenshots
    and tenInchScreenshots (Play doesn't distinguish the two from a single source
    image); everything else goes to phoneScreenshots."""
    return ["sevenInchScreenshots", "tenInchScreenshots"] if "tablet" in device_key else ["phoneScreenshots"]


def compose_all(cfg: StudioConfig, lang: str, only_device: str | None = None) -> list[Path]:
    """Renders raw captures straight into the real fastlane output locations —
    fastlane/screenshots/<locale>/ for iOS, fastlane/metadata/android/<locale>/images/
    <category>/ for Android — so there's no separate `.appstoresuite/<lang>/store/`
    copy for `push` to duplicate later."""
    devices = {only_device: cfg.devices[only_device]} if only_device else cfg.devices
    titles = titles_store.load_titles(cfg, lang)
    outputs: list[Path] = []
    lang_raw_dir = cfg.raw_dir_for(lang)
    raw_root = lang_raw_dir if lang_raw_dir.exists() else cfg.raw_dir

    ios_dest_dir = cfg.ios_screenshots_dir(lang)
    # Full recompose (only_device unset) starts the iOS/Android locale dirs clean,
    # mirroring the old push-time `--overwrite_screenshots` semantics — otherwise
    # shots removed/renamed since the last compose would linger and get uploaded
    # alongside the new ones. A single-device recompose leaves them alone so it
    # doesn't wipe other devices.
    if only_device is None and ios_dest_dir.exists():
        shutil.rmtree(ios_dest_dir)

    # Only clear the screenshot category subfolders, not the whole images/ dir —
    # that dir also holds icon.png/featureGraphic.png (written by `store-icon`/
    # `feature-graphic`, not by compose), which a blanket rmtree would silently
    # destroy on every recompose.
    android_images_dir = cfg.android_images_dir(lang)
    if only_device is None and android_images_dir.exists():
        for category_dir in ("phoneScreenshots", "sevenInchScreenshots", "tenInchScreenshots"):
            shutil.rmtree(android_images_dir / category_dir, ignore_errors=True)

    ios_n = 1
    for device_key, device in devices.items():
        device_raw_dir = raw_root / device_key
        if not device_raw_dir.exists():
            continue

        android_dest_dirs: dict[str, Path] = {}
        if device.kind == "android":
            android_dest_dirs = {c: android_images_dir / c for c in _android_categories(device_key)}

        for i, raw_path in enumerate(sorted(device_raw_dir.glob("*.png")), start=1):
            shot_id = raw_path.stem
            meta = titles.get(shot_id, {})
            title = meta.get("title") or shot_id.replace("_", " ").title()
            subtitle = meta.get("subtitle", "")
            style = style_choices.resolve_style(cfg, shot_id)

            if device.kind == "ios":
                dest = ios_dest_dir / f"{ios_n}_{device_key}_{shot_id}.png"
                ios_n += 1
                render_shot(
                    cfg, lang, device_key, device, shot_id, title, subtitle, raw_path,
                    style=style, dest_override=dest,
                )
                outputs.append(dest)
                print(f"  composed {dest}")
            else:
                categories = list(android_dest_dirs.items())
                _, primary_dir = categories[0]
                primary_dest = primary_dir / f"{i}_{shot_id}.png"
                render_shot(
                    cfg, lang, device_key, device, shot_id, title, subtitle, raw_path,
                    style=style, dest_override=primary_dest,
                )
                outputs.append(primary_dest)
                print(f"  composed {primary_dest}")
                for _, cat_dir in categories[1:]:
                    cat_dir.mkdir(parents=True, exist_ok=True)
                    extra_dest = cat_dir / f"{i}_{shot_id}.png"
                    shutil.copyfile(primary_dest, extra_dest)
                    outputs.append(extra_dest)
                    print(f"  composed {extra_dest}")
    return outputs
