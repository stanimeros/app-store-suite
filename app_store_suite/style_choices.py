from __future__ import annotations

import dataclasses
import json
import threading

from .config import StudioConfig, StyleConfig
from .style_variants import VARIANTS

_lock = threading.Lock()


def _path(cfg: StudioConfig):
    """Shared across languages — style (background/layout/decoration) is
    language-independent, only the rendered title/subtitle text differs."""
    return cfg.output_dir / "style_choices.json"


def load_choices(cfg: StudioConfig) -> dict[str, str]:
    path = _path(cfg)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_choice(cfg: StudioConfig, shot_id: str, variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown style variant '{variant}'; choices are {sorted(VARIANTS)}")
    with _lock:
        data = load_choices(cfg)
        data[shot_id] = variant
        path = _path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def clear_choice(cfg: StudioConfig, shot_id: str) -> None:
    with _lock:
        data = load_choices(cfg)
        if shot_id in data:
            del data[shot_id]
            _path(cfg).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def resolve_style(cfg: StudioConfig, shot_id: str) -> StyleConfig:
    """The config's own style, with this shot's chosen variant's overrides applied
    on top (if one was picked via `style-pick`)."""
    variant = load_choices(cfg).get(shot_id)
    if not variant:
        return cfg.style
    return dataclasses.replace(cfg.style, **VARIANTS.get(variant, {}))
