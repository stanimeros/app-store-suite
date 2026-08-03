from __future__ import annotations

import json
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

_BASE_URL = "https://raw.githubusercontent.com/fastlane/frameit-frames/gh-pages/latest/"


def cache_dir() -> Path:
    d = Path.home() / ".cache" / "screenshot-studio" / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(name: str, dest: Path) -> None:
    url = _BASE_URL + urllib.parse.quote(name)
    with urllib.request.urlopen(url, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Failed to fetch {url}: HTTP {resp.status}")
        dest.write_bytes(resp.read())


@lru_cache(maxsize=1)
def load_offsets() -> dict:
    """Returns the 'portrait' offsets map: {device_name: {'offset': '+x+y', 'width': int}}"""
    dest = cache_dir() / "offsets.json"
    if not dest.exists():
        _download("offsets.json", dest)
    return json.loads(dest.read_text())["portrait"]


def get_frame_path(frame_file: str) -> Path:
    """Returns a local path to the bezel PNG, downloading it into the cache if needed."""
    dest = cache_dir() / frame_file
    if not dest.exists():
        _download(frame_file, dest)
    return dest


def prefetch(frame_files: set[str]) -> list[str]:
    """Downloads offsets.json + each frame file. Returns the list actually fetched."""
    load_offsets()
    fetched = []
    for name in sorted(frame_files):
        dest = cache_dir() / name
        if not dest.exists():
            _download(name, dest)
            fetched.append(name)
    return fetched
