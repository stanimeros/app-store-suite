from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import devices as devices_mod
from . import titles_store
from .config import DeviceConfig, StudioConfig
from .frames import fetch as frames_fetch

_FONTS_DIR = Path(__file__).parent / "fonts"
_MARGIN_RATIO = 0.08  # side margin as a fraction of canvas width
_TITLE_AREA_RATIO = 0.22  # fraction of canvas height reserved for title text


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONTS_DIR / name), size)


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
    device_key: str,
    device: DeviceConfig,
    shot_id: str,
    title: str,
    subtitle: str,
    raw_path: Path,
) -> Path:
    canvas_w, canvas_h = devices_mod.store_resolution(device)
    bg_color = _hex_to_rgb(cfg.style.background_color)
    title_color = _hex_to_rgb(cfg.style.title_color)

    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(canvas)

    margin = round(canvas_w * _MARGIN_RATIO)
    title_area_h = round(canvas_h * _TITLE_AREA_RATIO)

    text_max_width = canvas_w - margin * 2

    title_font = _font(cfg.style.font_bold, round(canvas_w * 0.062))
    lines = _wrap_text(draw, title, title_font, text_max_width)
    line_height = title_font.size + round(title_font.size * 0.3)
    text_block_h = line_height * len(lines)

    sub_lines: list[str] = []
    sub_font: ImageFont.FreeTypeFont | None = None
    sub_line_height = 0
    sub_gap = 0
    if subtitle:
        sub_font = _font(cfg.style.font_regular, round(canvas_w * 0.032))
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

    raw = Image.open(raw_path)
    framed = _framed_device_image(raw, device)

    device_area_w = canvas_w - margin * 2
    device_area_h = canvas_h - title_area_h - margin
    scale = min(device_area_w / framed.width, device_area_h / framed.height)
    framed_resized = framed.resize(
        (round(framed.width * scale), round(framed.height * scale)), Image.LANCZOS
    )

    paste_x = round((canvas_w - framed_resized.width) / 2)
    paste_y = canvas_h - margin - framed_resized.height
    canvas.paste(framed_resized, (paste_x, paste_y), framed_resized)

    dest = cfg.store_dir / device_key / f"{shot_id}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest


def compose_all(cfg: StudioConfig, only_device: str | None = None) -> list[Path]:
    devices = {only_device: cfg.devices[only_device]} if only_device else cfg.devices
    titles = titles_store.load_titles(cfg)
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
            dest = render_shot(cfg, device_key, device, shot_id, title, subtitle, raw_path)
            outputs.append(dest)
            print(f"  composed {dest}")
    return outputs
