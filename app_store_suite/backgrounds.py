from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from .config import StudioConfig, parse_dotenv

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

# Feature graphic is Play's 1024x500 wide banner (no framed screenshot on it — just
# the app icon + a title/subtitle), so the prompt asks for a wide, calm banner rather
# than a phone-shaped background, and leans on the icon's own colors/mood as reference
# since there's no screenshot to riff on for it.
FEATURE_GRAPHIC_PROMPT = (
    "Generate a simple, clean wide banner background image inspired by the colors "
    "and mood of this app icon. Soft abstract shapes, gradients, or subtle visual "
    "motifs only — no text, no letters, no UI elements, no screenshots, no devices, "
    "nothing that looks like an app screen. It will sit behind an app icon and a "
    "marketing headline/subtitle running left to right, so keep it calm enough for "
    "text to stay readable across the whole width."
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
    its file is still on disk. None means compose skips this shot entirely — this is
    the normal state until generate-bgs + bg-pick have been run for a shot."""
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
    return parse_dotenv(cfg.app.flutter_dir / ".env").get(key)


def _generate_one(api_key: str, reference_path: Path, prompt: str) -> bytes:
    from openai import OpenAI  # deferred: only needed when generate-bgs actually runs

    client = OpenAI(api_key=api_key)
    with open(reference_path, "rb") as f:
        result = client.images.edit(
            model=MODEL,
            image=f,
            prompt=prompt,
        )
    return base64.b64decode(result.data[0].b64_json)


def generate_backgrounds(
    cfg: StudioConfig,
    device_key: str,
    lang: str,
    shot_ids: list[str] | None = None,
) -> list[Path]:
    """Generates one new numbered background set (numbering continues from whatever
    sets already exist on disk), one image per shot, via the OpenAI images API — each
    shot's own raw screenshot is passed in as the reference image (no mask), so the
    model riffs on its colors/mood instead of starting from a blank prompt. Nothing is
    picked automatically; run `bg-pick` afterwards to choose a set per shot.

    Deliberately one set per call, not a batch: review each set before generating the
    next, and pass a refined prompt/guide if you want another try at a shot, rather
    than generating many variants blind."""
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
    set_number = (max(existing) + 1) if existing else 1
    dest_dir = set_dir(cfg, set_number)
    dest_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for raw_path in raw_paths:
        shot_id = raw_path.stem
        print(f"  generating set{set_number}/{shot_id}...")
        png_bytes = _generate_one(api_key, raw_path, BACKGROUND_PROMPT)
        dest = dest_dir / f"{shot_id}.png"
        dest.write_bytes(png_bytes)
        written.append(dest)
    return written


# --- Feature graphic backgrounds -----------------------------------------------
#
# Separate from the per-shot screenshot backgrounds above: there's no raw
# screenshot to riff on (the feature graphic is just the app icon + a headline),
# there's only ever one feature graphic (not one per shot), and its canvas is a
# very different 1024x500 wide banner shape. Kept as its own small set/choice
# system mirroring the shot one, rather than shoehorning it into `generate_backgrounds`.


def _fg_choice_path(cfg: StudioConfig) -> Path:
    return cfg.output_dir / "feature_graphic_choice.json"


def load_fg_choice(cfg: StudioConfig) -> int | None:
    path = _fg_choice_path(cfg)
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("set")


def save_fg_choice(cfg: StudioConfig, set_number: int) -> None:
    path = _fg_choice_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"set": set_number}, indent=2))


def clear_fg_choice(cfg: StudioConfig) -> None:
    path = _fg_choice_path(cfg)
    if path.exists():
        path.unlink()


def fg_backgrounds_dir(cfg: StudioConfig) -> Path:
    return cfg.output_dir / "feature_graphic_backgrounds"


def fg_set_dir(cfg: StudioConfig, set_number: int) -> Path:
    return fg_backgrounds_dir(cfg) / f"set{set_number}"


def fg_available_sets(cfg: StudioConfig) -> list[int]:
    root = fg_backgrounds_dir(cfg)
    if not root.is_dir():
        return []
    sets = []
    for d in root.iterdir():
        match = re.fullmatch(r"set(\d+)", d.name)
        if match and d.is_dir():
            sets.append(int(match.group(1)))
    return sorted(sets)


def resolve_fg_background_path(cfg: StudioConfig) -> Path | None:
    """The feature graphic's chosen generated background (via `fg-bg-pick`), if one
    was picked and its file is still on disk. None means `feature-graphic` falls back
    to the plain style.background_color fill."""
    set_number = load_fg_choice(cfg)
    if set_number is None:
        return None
    path = fg_set_dir(cfg, set_number) / "background.png"
    return path if path.exists() else None


def generate_feature_graphic_background(cfg: StudioConfig) -> Path:
    """Generates one new numbered feature graphic background via the OpenAI images
    API, using the app icon as the reference image (no mask). Nothing is picked
    automatically; run `fg-bg-pick` afterwards to use it."""
    api_key = _env_value(cfg, "OPENAI_API_KEY")
    if not api_key:
        raise BackgroundGenerationError("OPENAI_API_KEY is not set (add it to .env or export it)")

    if not cfg.app.icon_source.exists():
        raise BackgroundGenerationError(f"icon_source not found: {cfg.app.icon_source}")

    existing = fg_available_sets(cfg)
    set_number = (max(existing) + 1) if existing else 1
    dest_dir = fg_set_dir(cfg, set_number)
    dest_dir.mkdir(parents=True, exist_ok=True)

    print(f"  generating feature graphic background set{set_number}...")
    png_bytes = _generate_one(api_key, cfg.app.icon_source, FEATURE_GRAPHIC_PROMPT)
    dest = dest_dir / "background.png"
    dest.write_bytes(png_bytes)
    return dest
