from __future__ import annotations

import hashlib
import io
import random
import re
from collections import Counter
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import devices as devices_mod
from . import titles_store
from .config import DeviceConfig, StudioConfig, StyleConfig
from .frames import fetch as frames_fetch

_FONTS_DIR = Path(__file__).parent / "fonts"
_MARGIN_RATIO = 0.08  # side margin as a fraction of canvas width
_TITLE_AREA_RATIO = 0.22  # fraction of canvas height reserved for title text

# Bundled fallback with broad script coverage (Greek, Cyrillic, etc.), used
# whenever the configured brand font (e.g. Poppins, which is Latin-only) is
# missing glyphs for the text being rendered.
_FALLBACK_FONTS = {"bold": "NotoSans-Bold.ttf", "regular": "NotoSans-Regular.ttf"}

_cmap_cache: dict[str, set[int]] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONTS_DIR / name), size)


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


def _parse_offset(offset: str) -> tuple[int, int]:
    match = re.match(r"\+(-?\d+)\+(-?\d+)", offset)
    if not match:
        raise ValueError(f"Unrecognized offset format: {offset!r}")
    return int(match.group(1)), int(match.group(2))


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
        color2 = _hex_to_rgb(style.gradient_color2) if style.gradient_color2 else tuple(
            min(255, c + 40) for c in color1
        )
        return _gradient_background(canvas_w, canvas_h, color1, color2)
    bg_color = _background_color_for(raw, style.background_color, style.background_mode)
    return Image.new("RGB", (canvas_w, canvas_h), bg_color)


def _apply_overlay(canvas: Image.Image, overlay: Image.Image) -> None:
    """Alpha-composites an RGBA overlay onto an RGB canvas in place."""
    composited = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    canvas.paste(composited, (0, 0))


def _draw_shape_decorations(canvas: Image.Image, style: StyleConfig, shot_id: str) -> None:
    """Soft, low-opacity blurred circles behind the device — no extra dependency."""
    rng = random.Random(_seed_for(shot_id))
    w, h = canvas.size
    color = _hex_to_rgb(style.decoration_color) if style.decoration_color else _hex_to_rgb(style.title_color)

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


def _draw_svg_decoration(canvas: Image.Image, style: StyleConfig, shot_id: str) -> None:
    """Rasterizes one .svg from decoration_svg_dir (picked deterministically per shot)
    behind the device, in the top-right corner at low opacity."""
    if not style.decoration_svg_dir or not style.decoration_svg_dir.is_dir():
        return
    files = sorted(style.decoration_svg_dir.glob("*.svg"))
    if not files:
        return

    import cairosvg  # deferred: only needed when decoration_svg is actually configured

    svg_path = files[_seed_for(shot_id) % len(files)]
    w, h = canvas.size
    target_h = round(h * 0.4)
    png_bytes = cairosvg.svg2png(url=str(svg_path), output_height=target_h)
    decoration = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    alpha = decoration.split()[3].point(lambda a: round(a * 0.16))
    decoration.putalpha(alpha)

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    px = w - decoration.width - round(w * 0.03)
    py = round(h * 0.03)
    overlay.paste(decoration, (px, py), decoration)
    _apply_overlay(canvas, overlay)


def _apply_decoration(canvas: Image.Image, style: StyleConfig, shot_id: str) -> None:
    if style.decoration == "shapes":
        _draw_shape_decorations(canvas, style, shot_id)
    elif style.decoration == "svg":
        _draw_svg_decoration(canvas, style, shot_id)


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
) -> Path:
    canvas_w, canvas_h = devices_mod.store_resolution(device)
    raw = Image.open(raw_path)
    title_color = _hex_to_rgb(cfg.style.title_color)

    canvas = _build_background(canvas_w, canvas_h, raw, cfg.style)
    _apply_decoration(canvas, cfg.style, shot_id)
    draw = ImageDraw.Draw(canvas)

    margin = round(canvas_w * _MARGIN_RATIO)
    title_area_h = round(canvas_h * _TITLE_AREA_RATIO)

    text_max_width = canvas_w - margin * 2

    title_font = _font_for_text(cfg.style.font_bold, "bold", round(canvas_w * 0.062), title)
    lines = _wrap_text(draw, title, title_font, text_max_width)
    line_height = title_font.size + round(title_font.size * 0.3)
    text_block_h = line_height * len(lines)

    sub_lines: list[str] = []
    sub_font: ImageFont.FreeTypeFont | None = None
    sub_line_height = 0
    sub_gap = 0
    if subtitle:
        sub_font = _font_for_text(cfg.style.font_regular, "regular", round(canvas_w * 0.032), subtitle)
        sub_lines = _wrap_text(draw, subtitle, sub_font, text_max_width)
        sub_line_height = sub_font.size + round(sub_font.size * 0.3)
        sub_gap = round(sub_font.size * 0.5)

    total_text_h = text_block_h + (sub_gap + sub_line_height * len(sub_lines) if sub_lines else 0)
    text_top = round((max(title_area_h, total_text_h) - total_text_h) / 2)

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

    title_area_h = max(title_area_h, total_text_h)

    framed = _framed_device_image(raw, device)

    device_area_w = canvas_w - margin * 2
    device_area_h = canvas_h - title_area_h - margin
    scale = min(device_area_w / framed.width, device_area_h / framed.height)
    framed_resized = framed.resize(
        (round(framed.width * scale), round(framed.height * scale)), Image.LANCZOS
    )

    if cfg.style.layout == "tilted":
        direction = 1 if _seed_for(shot_id) % 2 == 0 else -1
        rotated = framed_resized.rotate(
            cfg.style.tilt_degrees * direction, expand=True, resample=Image.BICUBIC
        )
        shrink = min(device_area_w / rotated.width, device_area_h / rotated.height, 1.0)
        if shrink < 1.0:
            rotated = rotated.resize(
                (round(rotated.width * shrink), round(rotated.height * shrink)), Image.LANCZOS
            )
        framed_resized = rotated

    paste_x = round((canvas_w - framed_resized.width) / 2)
    paste_y = canvas_h - margin - framed_resized.height
    canvas.paste(framed_resized, (paste_x, paste_y), framed_resized)

    dest = cfg.store_dir(lang) / device_key / f"{shot_id}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest


def compose_all(cfg: StudioConfig, lang: str, only_device: str | None = None) -> list[Path]:
    devices = {only_device: cfg.devices[only_device]} if only_device else cfg.devices
    titles = titles_store.load_titles(cfg, lang)
    outputs: list[Path] = []
    for device_key, device in devices.items():
        device_raw_dir = cfg.raw_dir / device_key
        if not device_raw_dir.exists():
            continue
        for raw_path in sorted(device_raw_dir.glob("*.png")):
            shot_id = raw_path.stem
            meta = titles.get(shot_id, {})
            title = meta.get("title") or shot_id.replace("_", " ").title()
            subtitle = meta.get("subtitle", "")
            dest = render_shot(cfg, lang, device_key, device, shot_id, title, subtitle, raw_path)
            outputs.append(dest)
            print(f"  composed {dest}")
    return outputs
