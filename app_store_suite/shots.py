from __future__ import annotations

from .config import StudioConfig


def discover_shot_ids(cfg: StudioConfig, lang: str | None = None) -> list[str]:
    """Union of shot ids captured so far across all devices, sorted by first-seen mtime."""
    seen: dict[str, float] = {}
    raw_dir = cfg.raw_dir_for(lang)
    if raw_dir.exists():
        for device_dir in raw_dir.iterdir():
            if not device_dir.is_dir():
                continue
            for png in device_dir.glob("*.png"):
                mtime = png.stat().st_mtime
                if png.stem not in seen or mtime < seen[png.stem]:
                    seen[png.stem] = mtime
    return sorted(seen, key=seen.get)


def captured_shot_ids(cfg: StudioConfig, device_key: str, lang: str | None = None) -> set[str]:
    device_dir = cfg.raw_dir_for(lang) / device_key
    if not device_dir.exists():
        return set()
    return {p.stem for p in device_dir.glob("*.png")}
