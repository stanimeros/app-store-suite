from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import AppConfig, StudioConfig

# KNOWN LIMITATION: every Android push below passes
# --changes_not_sent_for_review true, which is *supposed* to keep the edit as
# an unpublished draft in Play Console instead of auto-queueing it for
# Google's review. In practice, for edits that only touch listing content
# (metadata/images/screenshots — no APK/AAB) rather than a track/release,
# Google's backend appears to ignore the flag and sends it for review
# anyway. This is a confirmed, currently-open upstream fastlane bug, not
# something fixable here: https://github.com/fastlane/fastlane/issues/26439
# (screenshots sent for review automatically despite the flag, same
# metadata/images/screenshots-only scenario as this file's calls). Kept as
# a documented caveat rather than worked around — Play's review of listing
# content is normally fast/automated, unlike binary review, so this has
# been judged an acceptable tradeoff over disabling the push entirely.

class PushError(RuntimeError):
    pass


def _require(cmd: str) -> None:
    if shutil.which(cmd) is None:
        raise PushError(f"'{cmd}' not found on PATH")


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise PushError(f"command failed ({result.returncode}): {' '.join(cmd)}")


def _android_listing_version_code(cfg: StudioConfig) -> str:
    """Version code on the internal track — supply needs an existing release
    anchor for listing edits even when APK/changelogs/images are skipped."""
    result = subprocess.run(
        [
            "bundle", "exec", "fastlane", "run", "google_play_track_version_codes",
            f"json_key:{cfg.app.play_json_key}",
            f"package_name:{cfg.app.android_package_name}",
            "track:internal",
        ],
        cwd=cfg.app.flutter_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PushError(
            f"Could not read internal-track version codes: {result.stderr or result.stdout}"
        )
    codes = [int(m) for m in re.findall(r"\b(\d+)\b", result.stdout.split("Result:")[-1])]
    if not codes:
        raise PushError(
            "No release on Play internal track — upload a build before pushing listing metadata"
        )
    return str(max(codes))


def _require_android_creds(app: AppConfig) -> None:
    if not (app.android_package_name and app.play_json_key):
        raise PushError(
            "android_package_name/play_json_key not set in the config — required to push to Play Console"
        )


def _require_ios_creds(app: AppConfig) -> None:
    if not (app.bundle_id and app.asc_key_id and app.asc_issuer_id and app.asc_key_path):
        raise PushError(
            "bundle_id/asc_key_id/asc_issuer_id/asc_key_path not set in the config — "
            "required to push to App Store Connect"
        )


def _asc_api_key_file(app: AppConfig) -> Path:
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
        return Path(f.name)


def push_android_metadata(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Uploads each language's Play listing copy (title/short/full description) —
    no images/screenshots/APK — via `fastlane supply`. The text itself is written
    directly to fastlane/metadata/android/<locale>/*.txt by `store-listing`/
    `fetch-listing`; this just uploads what's already sitting there."""
    _require("bundle")
    _require_android_creds(cfg.app)
    langs = langs or cfg.languages

    metadata_root = cfg.app.flutter_dir / "fastlane" / "metadata" / "android"
    _run(
        [
            "bundle", "exec", "fastlane", "supply",
            "--package_name", cfg.app.android_package_name,
            "--json_key", str(cfg.app.play_json_key),
            "--metadata_path", str(metadata_root),
            "--skip_upload_apk", "true",
            "--skip_upload_aab", "true",
            "--skip_upload_images", "true",
            "--skip_upload_screenshots", "true",
            "--skip_upload_changelogs", "true",
            "--skip_upload_metadata", "false",
            "--track", "internal",
            "--version_code", _android_listing_version_code(cfg),
            "--changes_not_sent_for_review", "true",
        ],
        cwd=cfg.app.flutter_dir,
    )


def push_ios_metadata(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Uploads each language's App Store Connect listing copy (name/subtitle/
    promotional text/keywords/description) — no screenshots, no binary — via
    `fastlane deliver`. The text itself is written directly to
    fastlane/metadata/ios/<locale>/*.txt by `store-listing`/`fetch-listing`; this
    just uploads what's already sitting there.

    Known fastlane bug: on fastlane 2.237.0, `deliver`'s review-attachment
    step can crash with `Spaceship::ConnectAPI::Models.parse: No data` on
    apps that have never had an App Store version reviewed yet. If you hit
    that, patch the installed gem's deliver/lib/deliver/upload_metadata.rb
    `review_attachment_file` to rescue `version.fetch_app_store_review_detail`
    the same way the adjacent `fetch_reset_ratings_request` call already does
    (wrap in `begin/rescue; nil; end` and `return unless app_store_review_detail`).
    """
    _require("bundle")
    _require_ios_creds(cfg.app)
    langs = langs or cfg.languages

    metadata_root = cfg.app.flutter_dir / "fastlane" / "metadata" / "ios"
    api_key_path = _asc_api_key_file(cfg.app)
    try:
        _run(
            [
                "bundle", "exec", "fastlane", "deliver",
                "--app_identifier", cfg.app.bundle_id,
                "--api_key_path", str(api_key_path),
                "--metadata_path", str(metadata_root),
                "--skip_screenshots", "true",
                "--skip_metadata", "false",
                "--skip_binary_upload", "true",
                "--skip_app_version_update", "true",
                "--run_precheck_before_submit", "false",
                "--force",
            ],
            cwd=cfg.app.flutter_dir,
        )
    finally:
        api_key_path.unlink(missing_ok=True)


def push_android_images(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Uploads the Play Store icon (`store-icon`, shared across languages) and
    each language's feature graphic (`feature-graphic`) to Play Console — no
    screenshots, no metadata text, no binary. Both are separate asset slots
    from screenshots in Play's own data model (`images/icon.png` and
    `images/featureGraphic.png` under each locale), so `push_android_screenshots`
    never touches them — this is the only thing that does."""
    _require("bundle")
    _require_android_creds(cfg.app)
    langs = langs or cfg.languages

    metadata_root = cfg.app.flutter_dir / "fastlane" / "metadata" / "android"
    for lang in langs:
        images_dir = cfg.android_images_dir(lang)
        images_dir.mkdir(parents=True, exist_ok=True)
        if cfg.icon_path.exists():
            shutil.copyfile(cfg.icon_path, images_dir / "icon.png")
        fg_path = cfg.feature_graphic_path(lang)
        if fg_path.exists():
            shutil.copyfile(fg_path, images_dir / "featureGraphic.png")

    _run(
        [
            "bundle", "exec", "fastlane", "supply",
            "--package_name", cfg.app.android_package_name,
            "--json_key", str(cfg.app.play_json_key),
            "--metadata_path", str(metadata_root),
            "--skip_upload_apk", "true",
            "--skip_upload_aab", "true",
            "--skip_upload_images", "false",
            "--skip_upload_screenshots", "true",
            "--skip_upload_changelogs", "true",
            "--skip_upload_metadata", "true",
            "--track", "internal",
            # Without this, fastlane supply's default (false) sends every
            # change straight to Google's own review queue as part of
            # committing the edit — there is no separate "submit for review"
            # step to skip like there is on iOS, this *is* the equivalent.
            # true keeps the edit as an unpublished draft you review/publish
            # yourself in Play Console.
            "--changes_not_sent_for_review", "true",
        ],
        cwd=cfg.app.flutter_dir,
    )


def push_android_screenshots(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Uploads each language's composed screenshots (from `compose`) to Play
    Console — no metadata text, no icon/feature graphic. Devices whose config
    key contains "tablet" go to both sevenInchScreenshots and
    tenInchScreenshots (Play doesn't distinguish the two from a single
    source image); everything else goes to phoneScreenshots. `compose` already wrote
    the images directly to fastlane/metadata/android/<locale>/images/<category>/ —
    this just uploads what's already there."""
    _require("bundle")
    _require_android_creds(cfg.app)
    langs = langs or cfg.languages

    metadata_root = cfg.app.flutter_dir / "fastlane" / "metadata" / "android"
    _run(
        [
            "bundle", "exec", "fastlane", "supply",
            "--package_name", cfg.app.android_package_name,
            "--json_key", str(cfg.app.play_json_key),
            "--metadata_path", str(metadata_root),
            "--skip_upload_apk", "true",
            "--skip_upload_aab", "true",
            "--skip_upload_images", "true",
            "--skip_upload_screenshots", "false",
            "--skip_upload_changelogs", "true",
            "--skip_upload_metadata", "true",
            "--track", "internal",
            # Without this, fastlane supply's default (false) sends every
            # change straight to Google's own review queue as part of
            # committing the edit — there is no separate "submit for review"
            # step to skip like there is on iOS, this *is* the equivalent.
            # true keeps the edit as an unpublished draft you review/publish
            # yourself in Play Console.
            "--changes_not_sent_for_review", "true",
        ],
        cwd=cfg.app.flutter_dir,
    )


def push_ios_screenshots(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Uploads each language's composed screenshots to App Store Connect — no
    metadata text, no binary. `compose` already wrote them directly to
    fastlane/screenshots/<locale>/; `deliver` auto-detects each image's device-size
    bucket from its pixel dimensions, so every iOS device's shots for a language
    just sit together in one locale folder already.

    One `deliver` invocation per language, not one call covering every
    locale: `--overwrite_screenshots true` is supposed to delete a locale's
    existing screenshots before uploading its replacements, but observed in
    practice (multiple languages processed in parallel by a single deliver
    call) to sometimes undercount an existing bucket's occupancy and hit
    Apple's 10-screenshots-per-device-size limit with a mix of old and new
    images — e.g. some of a language's own new uploads getting rejected as
    "too many screenshots" despite only 6 being sent. Isolating each
    language into its own `deliver` call, each pointed at a screenshots_path
    containing only that locale's directory (copied into a scratch temp dir —
    the one remaining copy here is just to satisfy that isolation, not a
    second permanent copy of the screenshots), avoids whatever cross-locale
    interaction causes that miscount."""
    _require("bundle")
    _require_ios_creds(cfg.app)
    langs = langs or cfg.languages

    locale_dirs: dict[str, Path] = {}
    for lang in langs:
        locale = cfg.ios_locale(lang)
        locale_dir = cfg.ios_screenshots_dir(lang)
        if locale_dir.is_dir():
            locale_dirs[locale] = locale_dir

    metadata_path = cfg.app.flutter_dir / "fastlane" / "metadata" / "ios"
    api_key_path = _asc_api_key_file(cfg.app)
    try:
        for locale, locale_dir in locale_dirs.items():
            with tempfile.TemporaryDirectory() as tmp:
                scoped_root = Path(tmp) / "screenshots"
                scoped_root.mkdir()
                shutil.copytree(locale_dir, scoped_root / locale)
                _run(
                    [
                        "bundle", "exec", "fastlane", "deliver",
                        "--app_identifier", cfg.app.bundle_id,
                        "--api_key_path", str(api_key_path),
                        "--screenshots_path", str(scoped_root),
                        # Without an explicit --metadata_path, deliver still
                        # validates locale directory names against its own
                        # default (./fastlane/metadata, relative to cwd)
                        # even with --skip_metadata true — and that default
                        # contains our "android"/"ios" split, which it then
                        # rejects "ios" itself as an invalid locale name.
                        # Point it at the same fastlane/metadata/ios used by
                        # push_ios_metadata so it validates (and finds
                        # nothing to touch, since metadata is skipped) the
                        # right directory instead.
                        "--metadata_path", str(metadata_path),
                        "--skip_screenshots", "false",
                        "--skip_metadata", "true",
                        "--skip_binary_upload", "true",
                        "--skip_app_version_update", "true",
                        "--overwrite_screenshots", "true",
                        "--run_precheck_before_submit", "false",
                        "--force",
                    ],
                    cwd=cfg.app.flutter_dir,
                )
    finally:
        api_key_path.unlink(missing_ok=True)
