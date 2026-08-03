from __future__ import annotations

import json
import threading

from .config import StudioConfig

_lock = threading.Lock()


def load_titles(cfg: StudioConfig) -> dict:
    path = cfg.titles_path
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_title(cfg: StudioConfig, shot_id: str, title: str, subtitle: str = "") -> None:
    with _lock:
        data = load_titles(cfg)
        data[shot_id] = {"title": title, "subtitle": subtitle}
        cfg.titles_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.titles_path.write_text(json.dumps(data, indent=2))


def delete_title(cfg: StudioConfig, shot_id: str) -> None:
    with _lock:
        data = load_titles(cfg)
        if shot_id in data:
            del data[shot_id]
            cfg.titles_path.write_text(json.dumps(data, indent=2))
