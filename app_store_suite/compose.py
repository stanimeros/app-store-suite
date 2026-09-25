from __future__ import annotations

import colorsys
import hashlib
import math
import re
import shutil
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from . import backgrounds
from . import devices as devices_mod
from . import titles_store
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


def _accent_from_hue(hue: float, saturation: float, dark: bool) -> tuple[int, int, int]:
    """A vivid, saturated shade (`dark=True`) or a soft, gently-tinted shade
    (`dark=False`) of `hue` — e.g. a punchy royal blue for a sky-blue background,
    or a deep brick red for a coral one — instead of the generic near-black/white
    a plain luminance fallback would pick. `dark`'s value (0.42) is deliberately
    kept well above near-black (which reads as indistinguishable from plain black
    at a glance) while still low enough to contrast against a bright background."""
    if dark:
        r, g, b = colorsys.hsv_to_rgb(hue, min(1.0, saturation * 1.15 + 0.3), 0.42)
    else:
        r, g, b = colorsys.hsv_to_rgb(hue, min(0.35, saturation * 0.5), 0.96)
    return (round(r * 255), round(g * 255), round(b * 255))


def _readable_text_color(
    bg_rgb: tuple[int, int, int], preferred_rgb: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Picks a text color that stays visible against `bg_rgb`, preferring one that
    still reads as "on brand": a vivid, deliberately-chosen `preferred_rgb` (e.g. a
    brand accent set in the yaml) is kept whenever it already contrasts enough. A
    near-black/white "ink" preferred color is only kept as-is against a similarly
    neutral background — against a colorful one (an AI-generated background is
    rarely a flat neutral), a deeper/lighter shade of *that background's own hue*
    is used instead, so e.g. a blue background gets a rich matching navy rather
    than flat black. Only true neutral-on-neutral falls back to plain white/near-black."""
    contrasts = abs(_luminance(bg_rgb) - _luminance(preferred_rgb)) > 110
    bg_h, bg_s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in bg_rgb))
    _, pref_s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in preferred_rgb))

    if pref_s > 0.25 and contrasts:
        return preferred_rgb
    if bg_s > 0.15:
        return _accent_from_hue(bg_h, bg_s, dark=_luminance(bg_rgb) >= 128)
    if contrasts:
        return preferred_rgb
    return (255, 255, 255) if _luminance(bg_rgb) < 128 else (26, 26, 26)


def _sampled_text_color(
    canvas: Image.Image, preferred_rgb: tuple[int, int, int], left: int, top: int, right: int, bottom: int
) -> tuple[int, int, int]:
    """Samples the mean color of the exact region a text block will be drawn over
    and picks a readable color for it (see `_readable_text_color`). Clamps the
    region to the canvas bounds so a block computed slightly past the edge (e.g. a
    subtitle with no lines) doesn't hand Pillow an empty/invalid crop box."""
    box = (
        max(0, round(left)),
        max(0, round(top)),
        min(canvas.width, max(round(left) + 1, round(right))),
        min(canvas.height, max(round(top) + 1, round(bottom))),
    )
    band = ImageStat.Stat(canvas.crop(box)).mean[:3]
    return _readable_text_color(tuple(round(c) for c in band), preferred_rgb)


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


def _seed_for(shot_id: str) -> int:
    """Deterministic per-shot seed so tilt direction stays stable across
    re-composing the same shot (and consistent across devices/languages for it)."""
    return int(hashlib.md5(shot_id.encode()).hexdigest()[:8], 16)


def _cover_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scales `img` up just enough to cover a w x h canvas, then center-crops the
    overflow — same idea as CSS `background-size: cover`, since a generated
    background's own aspect ratio rarely matches the target store canvas exactly."""
    scale = max(w / img.width, h / img.height)
    resized = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    x = (resized.width - w) // 2
    y = (resized.height - h) // 2
    return resized.crop((x, y, x + w, y + h))


def _build_background(canvas_w: int, canvas_h: int, bg_image_path: Path) -> Image.Image:
    return _cover_resize(Image.open(bg_image_path).convert("RGB"), canvas_w, canvas_h)


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
    bg_image_path: Path | None = None,
) -> Path:
    """`style` defaults to `cfg.style`. `dest_override` is the file to write —
    required; compose_all points it directly at the real fastlane screenshots/images
    path. `bg_image_path` is the AI-generated background picked via `bg-pick` for
    this shot — required; compose_all skips shots that don't have one yet."""
    if dest_override is None:
        raise ValueError("render_shot requires dest_override — there is no default output path")
    if bg_image_path is None:
        raise ValueError("render_shot requires bg_image_path — no fallback background is rendered")
    style = style or cfg.style
    canvas_w, canvas_h = devices_mod.store_resolution(device)
    raw = Image.open(raw_path)

    canvas = _build_background(canvas_w, canvas_h, bg_image_path)

    margin = round(canvas_w * _MARGIN_RATIO)
    top_padding = round(canvas_h * _TOP_PADDING_RATIO)

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

    text_top = top_padding
    sub_top = text_top + text_block_h + sub_gap

    # Sampled from the actual region each block will sit on (not one shared guess for
    # both), so a background whose color/brightness varies across its height — much
    # more likely with an AI-generated image than a flat fill — still gets its own
    # visible color per block, matching the configured brand color whenever it
    # already contrasts enough against that region.
    title_color = _sampled_text_color(
        canvas, _hex_to_rgb(style.title_color), 0, text_top, canvas_w, text_top + text_block_h
    )
    sub_color = (
        _sampled_text_color(
            canvas,
            _hex_to_rgb(style.subtitle_color or style.title_color),
            0, sub_top, canvas_w, sub_top + sub_line_height * len(sub_lines),
        )
        if sub_lines
        else title_color
    )

    total_text_h = text_block_h + (sub_gap + sub_line_height * len(sub_lines) if sub_lines else 0)

    for i, line in enumerate(lines):
        w = draw.textlength(line, font=title_font)
        draw.text(
            ((canvas_w - w) / 2, text_top + i * line_height),
            line,
            font=title_font,
            fill=title_color,
        )

    if sub_lines:
        for i, line in enumerate(sub_lines):
            w = draw.textlength(line, font=sub_font)
            draw.text(
                ((canvas_w - w) / 2, sub_top + i * sub_line_height),
                line,
                font=sub_font,
                fill=sub_color,
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


_CONTACT_SHEET_THUMB_W = 300
_CONTACT_SHEET_LABEL_H = 28
_CONTACT_SHEET_GAP = 12


def _build_contact_sheet(image_paths_by_device: dict[str, list[Path]], dest: Path) -> Path | None:
    """A single grid image of every composed screenshot, for glancing at a whole
    batch (all devices/shots) at once instead of opening each file individually.
    One row per device, holding all of that device's shots side by side — however
    many there are (5, 6, 10, ...) — so a device's screenshots never spill onto a
    row shared with another device's."""
    device_rows = [paths for paths in image_paths_by_device.values() if paths]
    if not device_rows:
        return None

    label_font = ImageFont.truetype(str(_FONTS_DIR / "Inter-Regular.ttf"), 16)

    def _tile(path: Path) -> Image.Image:
        img = Image.open(path).convert("RGB")
        thumb_h = round(img.height * (_CONTACT_SHEET_THUMB_W / img.width))
        thumb = img.resize((_CONTACT_SHEET_THUMB_W, thumb_h), Image.LANCZOS)
        tile = Image.new(
            "RGB", (_CONTACT_SHEET_THUMB_W, thumb_h + _CONTACT_SHEET_LABEL_H), (240, 240, 240)
        )
        tile.paste(thumb, (0, 0))
        ImageDraw.Draw(tile).text(
            (4, thumb_h + 6), path.stem, font=label_font, fill=(20, 20, 20)
        )
        return tile

    tile_rows = [[_tile(p) for p in row] for row in device_rows]
    row_heights = [max(t.height for t in row) for row in tile_rows]
    columns = max(len(row) for row in tile_rows)
    sheet_w = columns * _CONTACT_SHEET_THUMB_W + (columns - 1) * _CONTACT_SHEET_GAP
    sheet_h = sum(row_heights) + (len(tile_rows) - 1) * _CONTACT_SHEET_GAP
    sheet = Image.new("RGB", (sheet_w, sheet_h), (30, 30, 30))

    y = 0
    for row_tiles, row_h in zip(tile_rows, row_heights):
        x = 0
        for tile in row_tiles:
            sheet.paste(tile, (x, y))
            x += _CONTACT_SHEET_THUMB_W + _CONTACT_SHEET_GAP
        y += row_h + _CONTACT_SHEET_GAP

    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def compose_all(cfg: StudioConfig, lang: str, only_device: str | None = None) -> list[Path]:
    """Renders raw captures straight into the real fastlane output locations —
    fastlane/screenshots/<locale>/ for iOS, fastlane/metadata/android/<locale>/images/
    <category>/ for Android — so there's no separate `.appstoresuite/<lang>/store/`
    copy for `push` to duplicate later."""
    devices = {only_device: cfg.devices[only_device]} if only_device else cfg.devices
    titles = titles_store.load_titles(cfg, lang)
    outputs: list[Path] = []
    # Excludes the tablet-category copies (same image, duplicated); grouped by
    # device so the contact sheet can give each device its own row.
    primary_outputs_by_device: dict[str, list[Path]] = {device_key: [] for device_key in devices}
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
    skipped_shots: set[str] = set()
    for device_key, device in devices.items():
        device_raw_dir = raw_root / device_key
        if not device_raw_dir.exists():
            continue

        android_dest_dirs: dict[str, Path] = {}
        if device.kind == "android":
            android_dest_dirs = {c: android_images_dir / c for c in _android_categories(device_key)}

        for i, raw_path in enumerate(sorted(device_raw_dir.glob("*.png")), start=1):
            shot_id = raw_path.stem
            bg_image_path = backgrounds.resolve_background_path(cfg, shot_id)
            if bg_image_path is None:
                if shot_id not in skipped_shots:
                    print(f"  skipped '{shot_id}': appstoresuite generate-bgs --config {cfg.config_path} --shot {shot_id}")
                    skipped_shots.add(shot_id)
                continue

            meta = titles.get(shot_id, {})
            title = meta.get("title") or shot_id.replace("_", " ").title()
            subtitle = meta.get("subtitle", "")

            if device.kind == "ios":
                dest = ios_dest_dir / f"{ios_n}_{device_key}_{shot_id}.png"
                ios_n += 1
                render_shot(
                    cfg, lang, device_key, device, shot_id, title, subtitle, raw_path,
                    dest_override=dest, bg_image_path=bg_image_path,
                )
                outputs.append(dest)
                primary_outputs_by_device[device_key].append(dest)
                print(f"  composed {dest}")
            else:
                categories = list(android_dest_dirs.items())
                _, primary_dir = categories[0]
                primary_dest = primary_dir / f"{i}_{shot_id}.png"
                render_shot(
                    cfg, lang, device_key, device, shot_id, title, subtitle, raw_path,
                    dest_override=primary_dest, bg_image_path=bg_image_path,
                )
                outputs.append(primary_dest)
                primary_outputs_by_device[device_key].append(primary_dest)
                print(f"  composed {primary_dest}")
                for _, cat_dir in categories[1:]:
                    cat_dir.mkdir(parents=True, exist_ok=True)
                    extra_dest = cat_dir / f"{i}_{shot_id}.png"
                    shutil.copyfile(primary_dest, extra_dest)
                    outputs.append(extra_dest)
                    print(f"  composed {extra_dest}")

    sheet_dest = cfg.output_dir / f"contact_sheet_{lang}.png"
    if _build_contact_sheet(primary_outputs_by_device, sheet_dest):
        print(f"  contact sheet: {sheet_dest}")

    return outputs
