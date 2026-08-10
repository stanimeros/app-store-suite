from __future__ import annotations

import json
import threading

from .config import StudioConfig

_lock = threading.Lock()


def load_titles(cfg: StudioConfig, lang: str) -> dict:
    path = cfg.titles_path(lang)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_title(cfg: StudioConfig, lang: str, shot_id: str, title: str, subtitle: str = "") -> None:
    with _lock:
        data = load_titles(cfg, lang)
        data[shot_id] = {"title": title, "subtitle": subtitle}
        path = cfg.titles_path(lang)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def delete_title(cfg: StudioConfig, lang: str, shot_id: str) -> None:
    with _lock:
        data = load_titles(cfg, lang)
        if shot_id in data:
            del data[shot_id]
            cfg.titles_path(lang).write_text(json.dumps(data, indent=2, ensure_ascii=False))
