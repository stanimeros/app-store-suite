from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from .config import StudioConfig

# gpt-image-2.5-flare is the fast, everyday tuning of OpenAI's current image model —
# right fit here since each shot only needs one straightforward edit pass, not the
# tighter multi-round control "sunburst" trades speed for.
MODEL = "gpt-image-2.5-flare"

BACKGROUND_PROMPT = (
    "Generate a simple, clean background image inspired by the colors and mood of "
    "this app screenshot. Soft abstract shapes, gradients, or subtle visual motifs "
    "only — no text, no letters, no UI elements, no screenshots, no devices, nothing "
    "that looks like an app screen. It will sit behind a framed screenshot and a "
    "marketing title, so keep the center uncluttered."
)


class BackgroundGenerationError(RuntimeError):
    pass


def _choices_path(cfg: StudioConfig) -> Path:
    return cfg.output_dir / "background_choices.json"


def load_choices(cfg: StudioConfig) -> dict[str, int]:
    path = _choices_path(cfg)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_choice(cfg: StudioConfig, shot_id: str, set_number: int) -> None:
    data = load_choices(cfg)
    data[shot_id] = set_number
    path = _choices_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def clear_choice(cfg: StudioConfig, shot_id: str) -> None:
    data = load_choices(cfg)
    if shot_id in data:
        del data[shot_id]
        _choices_path(cfg).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def backgrounds_dir(cfg: StudioConfig) -> Path:
    return cfg.output_dir / "backgrounds"


def set_dir(cfg: StudioConfig, set_number: int) -> Path:
    return backgrounds_dir(cfg) / f"set{set_number}"


def available_sets(cfg: StudioConfig) -> list[int]:
    root = backgrounds_dir(cfg)
    if not root.is_dir():
        return []
    sets = []
    for d in root.iterdir():
        match = re.fullmatch(r"set(\d+)", d.name)
        if match and d.is_dir():
            sets.append(int(match.group(1)))
    return sorted(sets)


def resolve_background_path(cfg: StudioConfig, shot_id: str) -> Path | None:
    """The shot's chosen generated background (via `bg-pick`), if one was picked and
    its file is still on disk. None means compose falls back to the plain
    background_color — this is the normal state until generate-bgs + bg-pick have
    been run for a shot."""
    set_number = load_choices(cfg).get(shot_id)
    if set_number is None:
        return None
    path = set_dir(cfg, set_number) / f"{shot_id}.png"
    return path if path.exists() else None


def _env_value(cfg: StudioConfig, key: str) -> str | None:
    """Same lookup order as ship.translate_arb: real environment first, then a plain
    KEY=value line in the project's own .env (gitignored, not exported to the shell)."""
    value = os.environ.get(key)
    if value:
        return value
    dotenv = cfg.app.flutter_dir / ".env"
    if not dotenv.exists():
        return None
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def _generate_one(api_key: str, raw_path: Path) -> bytes:
    from openai import OpenAI  # deferred: only needed when generate-bgs actually runs

    client = OpenAI(api_key=api_key)
    with open(raw_path, "rb") as f:
        result = client.images.edit(
            model=MODEL,
            image=f,
            prompt=BACKGROUND_PROMPT,
        )
    return base64.b64decode(result.data[0].b64_json)


def generate_backgrounds(
    cfg: StudioConfig,
    device_key: str,
    lang: str,
    shot_ids: list[str] | None = None,
    sets: int = 1,
) -> list[Path]:
    """Generates `sets` new numbered background sets (numbering continues from
    whatever sets already exist on disk), one image per shot per set, via the OpenAI
    images API — each shot's own raw screenshot is passed in as the reference image
    (no mask), so the model riffs on its colors/mood instead of starting from a blank
    prompt. Nothing is picked automatically; run `bg-pick` afterwards to choose a set
    per shot."""
    api_key = _env_value(cfg, "OPENAI_API_KEY")
    if not api_key:
        raise BackgroundGenerationError("OPENAI_API_KEY is not set (add it to .env or export it)")

    raw_dir = cfg.raw_dir_for(lang) / device_key
    raw_paths = sorted(raw_dir.glob("*.png"))
    if shot_ids:
        wanted = set(shot_ids)
        raw_paths = [p for p in raw_paths if p.stem in wanted]
    if not raw_paths:
        raise BackgroundGenerationError(f"No raw screenshots found in {raw_dir}")

    existing = available_sets(cfg)
    start = (max(existing) + 1) if existing else 1

    written: list[Path] = []
    for offset in range(sets):
        set_number = start + offset
        dest_dir = set_dir(cfg, set_number)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for raw_path in raw_paths:
            shot_id = raw_path.stem
            print(f"  generating set{set_number}/{shot_id}...")
            png_bytes = _generate_one(api_key, raw_path)
            dest = dest_dir / f"{shot_id}.png"
            dest.write_bytes(png_bytes)
            written.append(dest)
    return written
