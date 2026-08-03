from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .compose import _font_for_text, _hex_to_rgb, _wrap_text
from .config import StudioConfig

WIDTH, HEIGHT = 1024, 500


def generate_feature_graphic(cfg: StudioConfig, lang: str, headline: str, subtitle: str = "") -> Path:
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

    max_text_width = WIDTH - text_left - margin

    font = _font_for_text(cfg.style.font_bold, "bold", 64, headline)
    lines = _wrap_text(draw, headline, font, max_text_width)
    line_height = font.size + round(font.size * 0.25)
    block_h = line_height * len(lines)

    sub_lines: list[str] = []
    sub_font = None
    sub_line_height = 0
    sub_gap = 0
    if subtitle:
        sub_font = _font_for_text(cfg.style.font_regular, "regular", 28, subtitle)
        sub_lines = _wrap_text(draw, subtitle, sub_font, max_text_width)
        sub_line_height = sub_font.size + round(sub_font.size * 0.3)
        sub_gap = round(sub_font.size * 0.6)

    total_h = block_h + (sub_gap + sub_line_height * len(sub_lines) if sub_lines else 0)
    top = (HEIGHT - total_h) // 2
    for i, line in enumerate(lines):
        draw.text((text_left, top + i * line_height), line, font=font, fill=title_color)

    if sub_lines:
        sub_top = top + block_h + sub_gap
        for i, line in enumerate(sub_lines):
            draw.text((text_left, sub_top + i * sub_line_height), line, font=sub_font, fill=title_color)

    dest = cfg.feature_graphic_path(lang)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest
