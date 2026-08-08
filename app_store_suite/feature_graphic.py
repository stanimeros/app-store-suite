from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .compose import _font_for_text, _hex_to_rgb, _readable_text_color
from .config import StudioConfig

WIDTH, HEIGHT = 1024, 500


def _fit_single_line_font(
    draw: ImageDraw.ImageDraw,
    brand_font_name: str,
    weight: str,
    text: str,
    max_width: int,
    start_size: int,
    min_size: int,
) -> ImageFont.FreeTypeFont:
    """Shrinks from `start_size` down to `min_size` (in steps of 2px) until
    `text` fits on one line at `max_width` — feature graphics are a fixed
    1024x500 canvas with a wide range of possible app names/headlines, so
    wrapping to a second line easily collides with the subtitle below it or
    just looks cramped. A single shrink-to-fit line reads better than a
    wrapped one at this size. Falls back to `min_size` outright (still
    overflowing, in the worst case) rather than shrinking indefinitely.
    """
    size = start_size
    font = _font_for_text(brand_font_name, weight, size, text)
    while size > min_size and draw.textlength(text, font=font) > max_width:
        size -= 2
        font = _font_for_text(brand_font_name, weight, size, text)
    return font


def generate_feature_graphic(cfg: StudioConfig, lang: str, headline: str, subtitle: str = "") -> Path:
    bg_color = _hex_to_rgb(cfg.style.background_color)
    title_color = _readable_text_color(bg_color, _hex_to_rgb(cfg.style.title_color))

    canvas = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
    draw = ImageDraw.Draw(canvas)

    icon_size = 220
    margin = 64
    if cfg.app.icon_source.exists():
        icon = Image.open(cfg.app.icon_source).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
        mask = Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, icon_size, icon_size], radius=round(icon_size * 0.22), fill=255
        )
        canvas.paste(icon, (margin, (HEIGHT - icon_size) // 2), mask)
        text_left = margin + icon_size + 36
    else:
        text_left = margin

    max_text_width = WIDTH - text_left - margin

    font = _fit_single_line_font(
        draw, cfg.style.font_bold, "bold", headline, max_text_width, start_size=64, min_size=32
    )
    line_height = font.size + round(font.size * 0.25)
    block_h = line_height

    sub_font = None
    sub_line_height = 0
    sub_gap = 0
    if subtitle:
        sub_font = _fit_single_line_font(
            draw, cfg.style.font_regular, "regular", subtitle, max_text_width, start_size=28, min_size=16
        )
        sub_line_height = sub_font.size + round(sub_font.size * 0.3)
        sub_gap = round(sub_font.size * 0.6)

    total_h = block_h + (sub_gap + sub_line_height if subtitle else 0)
    top = (HEIGHT - total_h) // 2
    draw.text((text_left, top), headline, font=font, fill=title_color)

    if subtitle and sub_font:
        sub_top = top + block_h + sub_gap
        draw.text((text_left, sub_top), subtitle, font=sub_font, fill=title_color)

    dest = cfg.feature_graphic_path(lang)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest
