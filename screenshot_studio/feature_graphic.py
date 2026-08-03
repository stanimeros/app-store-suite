from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .compose import _font, _hex_to_rgb, _wrap_text
from .config import StudioConfig

WIDTH, HEIGHT = 1024, 500


def generate_feature_graphic(cfg: StudioConfig, headline: str) -> Path:
    bg_color = _hex_to_rgb(cfg.style.background_color)
    title_color = _hex_to_rgb(cfg.style.title_color)

    canvas = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
    draw = ImageDraw.Draw(canvas)

    icon_size = 260
    margin = 64
    if cfg.app.icon_source.exists():
        icon = Image.open(cfg.app.icon_source).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
        mask = Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, icon_size, icon_size], radius=round(icon_size * 0.22), fill=255
        )
        canvas.paste(icon, (margin, (HEIGHT - icon_size) // 2), mask)
        text_left = margin + icon_size + 56
    else:
        text_left = margin

    font = _font(cfg.style.font_bold, 64)
    max_text_width = WIDTH - text_left - margin
    lines = _wrap_text(draw, headline, font, max_text_width)
    line_height = font.size + round(font.size * 0.25)
    block_h = line_height * len(lines)
    top = (HEIGHT - block_h) // 2
    for i, line in enumerate(lines):
        draw.text((text_left, top + i * line_height), line, font=font, fill=title_color)

    dest = cfg.output_dir / "feature_graphic.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest
