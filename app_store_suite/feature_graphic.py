from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .compose import _build_background, _font_for_text, _hex_to_rgb, _sampled_text_color
from .config import StudioConfig

WIDTH, HEIGHT = 1024, 500


def _icon_with_transparency(icon_path: Path) -> Image.Image:
    """Most icon_source assets here are a flat-colored badge (e.g. a circular
    logo) on a solid, fully-opaque background rather than a true transparent
    PNG. Pasting that as-is (or under a plain rounded-rect mask) onto the
    feature graphic's own background leaves the icon's flat fill showing as a
    mismatched box around the badge. If the source already carries real
    transparency, use it untouched; otherwise flood-fill the background color
    from the corner (same approach as compose.py's device-frame silhouette
    mask) so only the badge shape survives compositing."""
    icon = Image.open(icon_path).convert("RGBA")
    alpha = icon.split()[3]
    if alpha.getextrema()[0] < 255:
        return icon

    rgb = icon.convert("RGB")
    ImageDraw.floodfill(rgb, (0, 0), (128, 0, 0), thresh=24)
    mask = rgb.split()[0].point(lambda r: 0 if r == 128 else 255)
    icon.putalpha(mask)
    return icon


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


def generate_feature_graphic(
    cfg: StudioConfig, lang: str, headline: str, subtitle: str = "", bg_image_path: Path | None = None
) -> Path:
    """`bg_image_path` is the AI-generated background picked via `fg-bg-pick`, if
    any — falls back to a plain `style.background_color` fill when None."""
    canvas = (
        _build_background(WIDTH, HEIGHT, bg_image_path)
        if bg_image_path
        else Image.new("RGB", (WIDTH, HEIGHT), _hex_to_rgb(cfg.style.background_color))
    )
    draw = ImageDraw.Draw(canvas)

    icon_size = 220
    margin = 64
    if cfg.app.icon_source.exists():
        icon = _icon_with_transparency(cfg.app.icon_source).resize((icon_size, icon_size), Image.LANCZOS)
        canvas.paste(icon, (margin, (HEIGHT - icon_size) // 2), icon)
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

    # Sampled from the actual region each block sits on (see compose.py's
    # render_shot for why) rather than one color for both, so a generated
    # background whose brightness varies across the banner still keeps both
    # lines visible, matching the configured brand color whenever it already
    # contrasts enough against that region.
    title_color = _sampled_text_color(
        canvas, _hex_to_rgb(cfg.style.title_color), text_left, top, WIDTH - margin, top + block_h
    )
    draw.text((text_left, top), headline, font=font, fill=title_color)

    if subtitle and sub_font:
        sub_top = top + block_h + sub_gap
        sub_color = _sampled_text_color(
            canvas,
            _hex_to_rgb(cfg.style.subtitle_color or cfg.style.title_color),
            text_left, sub_top, WIDTH - margin, sub_top + sub_line_height,
        )
        draw.text((text_left, sub_top), subtitle, font=sub_font, fill=sub_color)

    dest = cfg.feature_graphic_path(lang)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest
