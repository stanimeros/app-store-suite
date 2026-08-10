from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import titles_store
from .ai_titles import TitleSuggestionError, _language_name, run_claude_text
from .config import StudioConfig

# (json key, limit, platform, filename — filename is what `fastlane deliver`/`fastlane
# supply` itself reads/writes at fastlane/metadata/<platform>/<locale>/<filename>, so
# this is also the plain-text file each field lives at on disk. "current" (live store
# copy) is whatever's last committed to that path in git; "proposed" (drafted/hand-
# edited, not yet pushed) is the working-tree version — `git diff -- fastlane/metadata/`
# on the app's repo IS the current-vs-proposed comparison, no separate JSON needed.
_FIELDS = [
    ("play_app_name", 30, "android", "title.txt"),
    ("play_short_description", 80, "android", "short_description.txt"),
    ("play_full_description", 4000, "android", "full_description.txt"),
    ("ios_app_name", 30, "ios", "name.txt"),
    ("ios_subtitle", 30, "ios", "subtitle.txt"),
    ("ios_promotional_text", 170, "ios", "promotional_text.txt"),
    ("ios_keywords", 100, "ios", "keywords.txt"),
    ("ios_description", 4000, "ios", "description.txt"),
]
_LIMITS = {key: limit for key, limit, _, _ in _FIELDS}

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


def _parse_response(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise TitleSuggestionError(f"Could not parse a JSON response from: {raw!r}")
    data = json.loads(raw[start : end + 1])
    missing = [key for key, _, _, _ in _FIELDS if key not in data]
    if missing:
        raise TitleSuggestionError(f"Response missing keys {missing}: {raw!r}")
    return data


def field_path(cfg: StudioConfig, lang: str, key: str) -> Path:
    for field_key, _, platform, filename in _FIELDS:
        if field_key == key:
            base = cfg.android_metadata_dir(lang) if platform == "android" else cfg.ios_metadata_dir(lang)
            return base / filename
    raise ValueError(f"Unknown listing field '{key}'")


def read_field(cfg: StudioConfig, lang: str, key: str) -> str:
    """Working-tree ("proposed") text for one field — empty string if never written."""
    path = field_path(cfg, lang, key)
    return path.read_text().strip() if path.exists() else ""


def read_listing(cfg: StudioConfig, lang: str) -> dict[str, str]:
    """All fields' current working-tree text, keyed by field name — the "proposed"
    side of the current/proposed comparison. For "current" (live), read the same
    paths via `git show HEAD:<path>` in cfg.app.flutter_dir, or run `git diff --
    fastlane/metadata/` there to see the difference directly."""
    return {key: read_field(cfg, lang, key) for key, _, _, _ in _FIELDS}


def save_proposed(cfg: StudioConfig, lang: str, proposed: dict[str, str]) -> list[Path]:
    """Writes hand-edited text (e.g. from a UI) straight to each field's real fastlane
    path. Only keys present in `proposed` are touched; everything else is left as-is."""
    written: list[Path] = []
    for key, _, _, _ in _FIELDS:
        if key not in proposed:
            continue
        path = field_path(cfg, lang, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(proposed[key])
        written.append(path)
    return written


_MAX_ATTEMPTS = 4


def generate_store_listing(cfg: StudioConfig, lang: str, timeout: float = 180) -> list[Path]:
    """Drafts listing copy via the `claude` CLI and writes every field straight to its
    real fastlane/metadata/<platform>/<locale>/<field>.txt path (overwriting whatever
    was there — i.e. this replaces the "proposed" working-tree copy; `git diff` still
    shows it against whatever was last committed via `fetch-listing`)."""
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
        counts = {key: len(data[key]) for key, _, _, _ in _FIELDS}
        over_limit = [key for key, limit, _, _ in _FIELDS if counts[key] > limit]
        if not over_limit:
            break

        overage = "; ".join(
            f"{key}: {counts[key]} chars, limit is {_LIMITS[key]} — shorten by at least {counts[key] - _LIMITS[key]}"
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
        counts = {key: len(data[key]) for key, _, _, _ in _FIELDS}
        raise TitleSuggestionError(
            f"Could not get response under character limits after {_MAX_ATTEMPTS} attempts, "
            "still over on: "
            + ", ".join(f"{key} ({counts[key]}/{_LIMITS[key]})" for key in over_limit)
        )

    return save_proposed(cfg, lang, data)


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


def _adopt_locale_dir(root: Path, resolved: str, canonical: str) -> Path:
    """`deliver`/`supply` name their downloaded locale directory whatever the store
    itself calls it, which may not match the locale name our own `android_locale`/
    `ios_locale` (and therefore `compose`/`push`) use for this language — rename it
    into place so everything agrees on one directory per language."""
    if resolved == canonical:
        return root / resolved
    src, dest = root / resolved, root / canonical
    if dest.exists():
        for item in dest.iterdir():
            if item.is_file():
                item.unlink()
    else:
        dest.mkdir(parents=True)
    for item in src.iterdir():
        item.rename(dest / item.name)
    src.rmdir()
    return dest


def _prune_and_collect(locale_dir: Path, known_filenames) -> list[Path]:
    """`deliver`/`supply` download dozens of per-field .txt files we don't use
    (marketing_url, privacy_url, ...) — keep only the ones we actually read/write,
    so the committed baseline stays limited to real listing fields. Leaves
    subdirectories (e.g. Play's per-locale `images/`) untouched."""
    known = set(known_filenames)
    kept: list[Path] = []
    for item in locale_dir.iterdir():
        if not item.is_file():
            continue
        if item.name in known:
            kept.append(item)
        else:
            item.unlink()
    return kept


def _prune_unrelated_locales(metadata_root: Path, keep: set[str]) -> None:
    """`download_metadata`/`supply init` fetch every locale the store has, not just
    ours — drop directories for locales that aren't any of this app's configured
    languages so they don't clutter the repo with content nobody's managing here."""
    if not metadata_root.is_dir():
        return
    for item in metadata_root.iterdir():
        if item.is_dir() and item.name not in keep:
            shutil.rmtree(item)


def fetch_current_listing(cfg: StudioConfig, lang: str, timeout: float = 180) -> list[Path]:
    """Downloads the currently-live listing copy from App Store Connect (via `fastlane
    deliver`) and/or Play Console (via `fastlane supply`) directly into
    fastlane/metadata/<platform>/<locale>/*.txt, then stages+commits those paths as a
    baseline (if the flutter_dir is a git repo and anything changed) — so a later
    `git diff -- fastlane/metadata/` shows only local edits made since this fetch."""
    app = cfg.app
    has_ios = bool(app.bundle_id and app.asc_key_id and app.asc_issuer_id and app.asc_key_path)
    has_android = bool(app.android_package_name and app.play_json_key)
    if not has_ios and not has_android:
        raise FetchListingError(
            "No store credentials configured (bundle_id/asc_key_id/asc_issuer_id/asc_key_path "
            "for iOS, android_package_name/play_json_key for Android) — see the example config's "
            "'Required only for fetch-listing' section."
        )

    override = cfg.store_locales.get(lang)
    fetched: list[Path] = []

    if has_ios:
        fetched += _fetch_ios(app, cfg, lang, override)
    if has_android:
        fetched += _fetch_android(app, cfg, lang, override)

    _commit_baseline(app.flutter_dir, fetched, lang)
    return fetched


def _fetch_ios(app, cfg: StudioConfig, lang: str, override: str | None) -> list[Path]:
    ios_field_files = {filename for key, _, platform, filename in _FIELDS if platform == "ios"}
    metadata_root = app.flutter_dir / "fastlane" / "metadata" / "ios"

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
                "--metadata_path", str(metadata_root),
                "--force",
            ],
            cwd=app.flutter_dir,
        )
    finally:
        Path(api_key_path).unlink(missing_ok=True)

    resolved = _resolve_locale(metadata_root, lang, override)
    if resolved is None:
        available = sorted(p.name for p in metadata_root.iterdir()) if metadata_root.is_dir() else []
        raise FetchListingError(
            f"No App Store Connect locale matched '{lang}' under {metadata_root} "
            f"(available: {available or 'none downloaded'}) — set store_locales.{lang} in the config."
        )
    canonical = cfg.ios_locale(lang)
    locale_dir = _adopt_locale_dir(metadata_root, resolved, canonical)
    keep_locales = {cfg.ios_locale(l) for l in cfg.languages}
    _prune_unrelated_locales(metadata_root, keep_locales)
    return _prune_and_collect(locale_dir, ios_field_files)


def _fetch_android(app, cfg: StudioConfig, lang: str, override: str | None) -> list[Path]:
    android_field_files = {filename for key, _, platform, filename in _FIELDS if platform == "android"}
    metadata_root = app.flutter_dir / "fastlane" / "metadata" / "android"

    _run(
        [
            "bundle", "exec", "fastlane", "supply", "init",
            "--package_name", app.android_package_name,
            "--json_key", str(app.play_json_key),
        ],
        cwd=app.flutter_dir,
    )

    resolved = _resolve_locale(metadata_root, lang, override)
    if resolved is None:
        available = sorted(p.name for p in metadata_root.iterdir()) if metadata_root.is_dir() else []
        raise FetchListingError(
            f"No Play Console locale matched '{lang}' under {metadata_root} "
            f"(available: {available or 'none downloaded'}) — set store_locales.{lang} in the config."
        )
    canonical = cfg.android_locale(lang)
    locale_dir = _adopt_locale_dir(metadata_root, resolved, canonical)
    keep_locales = {cfg.android_locale(l) for l in cfg.languages}
    _prune_unrelated_locales(metadata_root, keep_locales)
    return _prune_and_collect(locale_dir, android_field_files)


def _commit_baseline(flutter_dir: Path, paths: list[Path], lang: str) -> None:
    if not paths:
        return
    check = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=flutter_dir, capture_output=True, text=True
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        return
    rel_paths = [str(p.relative_to(flutter_dir)) for p in paths]
    add = subprocess.run(["git", "add", *rel_paths], cwd=flutter_dir, capture_output=True, text=True)
    if add.returncode != 0:
        print(f"  warning: git add failed, baseline not committed: {add.stderr.strip()}")
        return
    still_clean = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=flutter_dir)
    if still_clean.returncode == 0:
        return
    commit = subprocess.run(
        ["git", "commit", "-m", f"fetch-listing: {lang} baseline from live stores"],
        cwd=flutter_dir,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        print(f"  warning: git commit failed, baseline not committed: {commit.stderr.strip()}")
