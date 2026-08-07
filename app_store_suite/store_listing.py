from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import titles_store
from .ai_titles import TitleSuggestionError, _language_name, run_claude_text
from .config import StudioConfig

# (json key, limit)
_FIELDS = [
    ("play_app_name", 30),
    ("play_short_description", 80),
    ("play_full_description", 4000),
    ("ios_app_name", 30),
    ("ios_subtitle", 30),
    ("ios_promotional_text", 170),
    ("ios_keywords", 100),
    ("ios_description", 4000),
]

PROMPT_TEMPLATE = (
    "Write Google Play and App Store listing copy for the Flutter app '{app_name}', "
    "in {language}. Base it on these captured feature screenshots (shot id -> title/"
    "subtitle already used on the store screenshots themselves):\n\n{shots_json}\n\n"
    "Do not name specific companies, brands, or providers visible on screen (e.g. "
    "Google, Apple, Facebook) since those can differ between platforms and builds.\n\n"
    "Respond with ONLY compact JSON on a single line, no markdown, no commentary, "
    "with exactly these keys and character limits (stay under every limit — this is "
    "hard-checked by code, not judged by you):\n"
    '{{"play_app_name": "...", // <=30 chars\n'
    '"play_short_description": "...", // <=80 chars\n'
    '"play_full_description": "...", // <=4000 chars\n'
    '"ios_app_name": "...", // <=30 chars\n'
    '"ios_subtitle": "...", // <=30 chars\n'
    '"ios_promotional_text": "...", // <=170 chars\n'
    '"ios_keywords": "...", // <=100 chars, comma-separated, no spaces after commas\n'
    '"ios_description": "..."}} // <=4000 chars\n'
    "play_app_name and ios_app_name may repeat the same text. play_full_description "
    "and ios_description may repeat the same text."
)

# Field -> filename downloaded by `fastlane deliver`/`fastlane supply` for current listing copy.
_IOS_METADATA_FILES = {
    "ios_app_name": "name.txt",
    "ios_subtitle": "subtitle.txt",
    "ios_promotional_text": "promotional_text.txt",
    "ios_keywords": "keywords.txt",
    "ios_description": "description.txt",
}
_ANDROID_METADATA_FILES = {
    "play_app_name": "title.txt",
    "play_short_description": "short_description.txt",
    "play_full_description": "full_description.txt",
}


def _parse_response(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise TitleSuggestionError(f"Could not parse a JSON response from: {raw!r}")
    data = json.loads(raw[start : end + 1])
    missing = [key for key, _ in _FIELDS if key not in data]
    if missing:
        raise TitleSuggestionError(f"Response missing keys {missing}: {raw!r}")
    return data


def load_listing(cfg: StudioConfig, lang: str) -> dict[str, dict]:
    """{field: {"current": str, "proposed": str, "count": int, "limit": int}}"""
    path = cfg.store_listing_path(lang)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_listing(cfg: StudioConfig, lang: str, listing: dict[str, dict]) -> Path:
    dest = cfg.store_listing_path(lang)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(listing, indent=2, ensure_ascii=False))
    return dest


def save_proposed(cfg: StudioConfig, lang: str, proposed: dict[str, str]) -> Path:
    """Saves hand-edited "proposed" text (e.g. from the web UI), preserving "current"."""
    existing = load_listing(cfg, lang)
    listing = dict(existing)
    for key, limit in _FIELDS:
        text = proposed.get(key, existing.get(key, {}).get("proposed", ""))
        listing[key] = {
            "current": existing.get(key, {}).get("current", ""),
            "proposed": text,
            "count": len(text),
            "limit": limit,
        }
    return _save_listing(cfg, lang, listing)


_MAX_ATTEMPTS = 4


def generate_store_listing(cfg: StudioConfig, lang: str, timeout: float = 180) -> Path:
    """Drafts "proposed" copy via the `claude` CLI, preserving any "current" values
    already pulled from the stores by `fetch_current_listing`."""
    shots = titles_store.load_titles(cfg, lang)
    if not shots:
        raise TitleSuggestionError(
            f"No shot titles found for '{lang}' at {cfg.titles_path(lang)} — capture and "
            "title some shots first."
        )

    prompt = PROMPT_TEMPLATE.format(
        app_name=cfg.app.name,
        language=_language_name(lang),
        shots_json=json.dumps(shots, ensure_ascii=False, indent=2),
    )

    data: dict | None = None
    over_limit: list[str] = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        data = _parse_response(run_claude_text(prompt, timeout))

        # Character counts are computed here in Python, never trusted from the model —
        # some languages (e.g. Greek) run longer per word than the model expects, so it
        # reliably overshoots tight limits like the 30-char iOS subtitle.
        counts = {key: len(data[key]) for key, _ in _FIELDS}
        over_limit = [key for key, limit in _FIELDS if counts[key] > limit]
        if not over_limit:
            break

        limits = dict(_FIELDS)
        overage = "; ".join(
            f"{key}: {counts[key]} chars, limit is {limits[key]} — shorten by at least {counts[key] - limits[key]}"
            for key in over_limit
        )
        prompt = (
            f"Your previous JSON response exceeded the character limit on these fields: "
            f"{overage}. Rewrite ONLY those field(s), keeping the rest identical, and "
            f"respond again with the full JSON object in the same shape as before, still "
            f"ONLY compact JSON on a single line, no markdown, no commentary. Previous "
            f"response was: {json.dumps(data, ensure_ascii=False)}"
        )
    else:
        limits = dict(_FIELDS)
        counts = {key: len(data[key]) for key, _ in _FIELDS}
        raise TitleSuggestionError(
            f"Could not get response under character limits after {_MAX_ATTEMPTS} attempts, "
            "still over on: "
            + ", ".join(f"{key} ({counts[key]}/{limits[key]})" for key in over_limit)
        )

    existing = load_listing(cfg, lang)
    listing = {
        key: {
            "current": existing.get(key, {}).get("current", ""),
            "proposed": data[key],
            "count": len(data[key]),
            "limit": limit,
        }
        for key, limit in _FIELDS
    }
    return _save_listing(cfg, lang, listing)


class FetchListingError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if result.returncode != 0:
        raise FetchListingError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr or result.stdout}"
        )


def _resolve_locale(base_dir: Path, lang: str, override: str | None) -> str | None:
    """Tries override first (if given — useful when the two stores use different
    codes for the same language, e.g. Play's "el-GR" vs App Store Connect's plain
    "el"), then the language code as-is, then a few common variants."""
    candidates = ([override] if override else []) + [lang]
    if lang == "en":
        candidates += ["en-US", "en-GB", "en-AU"]
    for candidate in candidates:
        if candidate and (base_dir / candidate).is_dir():
            return candidate
    return None


def _read_fields(locale_dir: Path, file_map: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field, filename in file_map.items():
        file_path = locale_dir / filename
        if file_path.exists():
            values[field] = file_path.read_text().strip()
    return values


def fetch_current_listing(cfg: StudioConfig, lang: str, timeout: float = 180) -> Path:
    """Downloads the currently-live listing copy from App Store Connect (via
    `fastlane deliver`) and/or Play Console (via `fastlane supply`) and merges it
    into this language's "current" values, leaving "proposed" untouched."""
    app = cfg.app
    has_ios = bool(app.bundle_id and app.asc_key_id and app.asc_issuer_id and app.asc_key_path)
    has_android = bool(app.android_package_name and app.play_json_key)
    if not has_ios and not has_android:
        raise FetchListingError(
            "No store credentials configured (bundle_id/asc_key_id/asc_issuer_id/asc_key_path "
            "for iOS, android_package_name/play_json_key for Android) — see the example config's "
            "'Required only for fetch-listing' section."
        )

    fetched: dict[str, str] = {}
    override = cfg.store_locales.get(lang)

    # `deliver`/`supply` dump dozens of per-field .txt files (most unused by us) into
    # the app's own repo as a scratch working dir. Our JSON is the source of truth, so
    # once we've read what we need, remove this dir again — unless it already existed
    # before this call (e.g. the app genuinely uses it for its own metadata uploads).
    metadata_dir = app.flutter_dir / "fastlane" / "metadata"
    preexisting = metadata_dir.is_dir()

    try:
        _fetch_into(app, lang, override, has_ios, has_android, fetched)
    finally:
        if not preexisting and metadata_dir.is_dir():
            shutil.rmtree(metadata_dir)

    existing = load_listing(cfg, lang)
    listing = dict(existing)
    for key, limit in _FIELDS:
        prior = existing.get(key, {})
        listing[key] = {
            "current": fetched.get(key, prior.get("current", "")),
            "proposed": prior.get("proposed", ""),
            "count": len(prior.get("proposed", "")),
            "limit": limit,
        }
    return _save_listing(cfg, lang, listing)


def _fetch_into(
    app, lang: str, override: str | None, has_ios: bool, has_android: bool, fetched: dict[str, str],
) -> None:
    if has_ios:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "key_id": app.asc_key_id,
                    "issuer_id": app.asc_issuer_id,
                    "key": app.asc_key_path.read_text(),
                    "in_house": False,
                },
                f,
            )
            api_key_path = f.name
        try:
            _run(
                [
                    "bundle", "exec", "fastlane", "deliver", "download_metadata",
                    "--app_identifier", app.bundle_id,
                    "--api_key_path", api_key_path,
                    "--force",
                ],
                cwd=app.flutter_dir,
            )
        finally:
            Path(api_key_path).unlink(missing_ok=True)

        metadata_root = app.flutter_dir / "fastlane" / "metadata"
        locale = _resolve_locale(metadata_root, lang, override)
        if locale is None:
            available = sorted(p.name for p in metadata_root.iterdir()) if metadata_root.is_dir() else []
            raise FetchListingError(
                f"No App Store Connect locale matched '{lang}' under {metadata_root} "
                f"(available: {available or 'none downloaded'}) — set store_locales.{lang} in the config."
            )
        fetched.update(_read_fields(metadata_root / locale, _IOS_METADATA_FILES))

    if has_android:
        _run(
            [
                "bundle", "exec", "fastlane", "supply", "init",
                "--package_name", app.android_package_name,
                "--json_key", str(app.play_json_key),
            ],
            cwd=app.flutter_dir,
        )

        metadata_root = app.flutter_dir / "fastlane" / "metadata" / "android"
        locale = _resolve_locale(metadata_root, lang, override)
        if locale is None:
            available = sorted(p.name for p in metadata_root.iterdir()) if metadata_root.is_dir() else []
            raise FetchListingError(
                f"No Play Console locale matched '{lang}' under {metadata_root} "
                f"(available: {available or 'none downloaded'}) — set store_locales.{lang} in the config."
            )
        fetched.update(_read_fields(metadata_root / locale, _ANDROID_METADATA_FILES))
