from __future__ import annotations

from pathlib import Path

from PIL import Image

from .config import StudioConfig

PLAY_STORE_ICON_SIZE = 512  # Google Play Console's required app icon size


def generate_play_store_icon(cfg: StudioConfig) -> Path:
    if not cfg.app.icon_source.exists():
        raise FileNotFoundError(f"Icon source not found: {cfg.app.icon_source}")

    with Image.open(cfg.app.icon_source) as img:
        if img.width < PLAY_STORE_ICON_SIZE or img.height < PLAY_STORE_ICON_SIZE:
            raise ValueError(
                f"Icon source is {img.width}x{img.height}; "
                f"needs to be at least {PLAY_STORE_ICON_SIZE}x{PLAY_STORE_ICON_SIZE}."
            )
        resized = img.convert("RGBA").resize(
            (PLAY_STORE_ICON_SIZE, PLAY_STORE_ICON_SIZE), Image.LANCZOS
        )

    dest = cfg.icon_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    resized.save(dest)
    return dest
