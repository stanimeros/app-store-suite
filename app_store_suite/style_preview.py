"""Renders every named style variant for one or more shots side by side, so you can
compare and pick a favorite per shot (via `style-pick`) without hand-editing the
config's style block. Style only affects background/layout/decoration, not the
per-language title/subtitle text, so previews only need one device + one language."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import titles_store
from .compose import render_shot
from .config import StudioConfig
from .style_variants import VARIANTS

_FONTS_DIR = Path(__file__).parent / "fonts"
_LABEL_HEIGHT = 44
_PANEL_GAP = 12


def generate_previews(
    cfg: StudioConfig, device_key: str, lang: str, shot_ids: list[str] | None = None
) -> list[Path]:
    device = cfg.devices[device_key]
    titles = titles_store.load_titles(cfg, lang)
    raw_paths = sorted((cfg.raw_dir / device_key).glob("*.png"))
    if shot_ids:
        wanted = set(shot_ids)
        raw_paths = [p for p in raw_paths if p.stem in wanted]

    preview_root = cfg.output_dir / "variant_previews"
    label_font = ImageFont.truetype(str(_FONTS_DIR / "Inter-Regular.ttf"), 28)

    sheets: list[Path] = []
    for raw_path in raw_paths:
        shot_id = raw_path.stem
        meta = titles.get(shot_id, {})
        title = meta.get("title") or shot_id.replace("_", " ").title()
        subtitle = meta.get("subtitle", "")

        panels: list[Image.Image] = []
        for variant_name, overrides in VARIANTS.items():
            variant_style = dataclasses.replace(cfg.style, **overrides)
            dest = preview_root / variant_name / device_key / f"{shot_id}.png"

            rendered = render_shot(
                cfg, lang, device_key, device, shot_id, title, subtitle, raw_path,
                style=variant_style, dest_override=dest,
            )
            panel = Image.open(rendered).convert("RGB")
            labeled = Image.new("RGB", (panel.width, panel.height + _LABEL_HEIGHT), (30, 30, 30))
            labeled.paste(panel, (0, _LABEL_HEIGHT))
            ImageDraw.Draw(labeled).text((16, 8), variant_name, font=label_font, fill=(255, 255, 255))
            panels.append(labeled)

        sheet_w = sum(p.width for p in panels) + _PANEL_GAP * (len(panels) - 1)
        sheet_h = max(p.height for p in panels)
        sheet = Image.new("RGB", (sheet_w, sheet_h), (255, 255, 255))
        x = 0
        for p in panels:
            sheet.paste(p, (x, 0))
            x += p.width + _PANEL_GAP
        sheet_path = preview_root / f"{shot_id}_compare.png"
        sheet.save(sheet_path)
        sheets.append(sheet_path)

    return sheets
