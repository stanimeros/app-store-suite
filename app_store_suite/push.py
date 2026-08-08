from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import AppConfig, StudioConfig
from .store_listing import load_listing

_PLAY_FIELDS = {
    "play_app_name": "title.txt",
    "play_short_description": "short_description.txt",
    "play_full_description": "full_description.txt",
}
_IOS_FIELDS = {
    "ios_app_name": "name.txt",
    "ios_subtitle": "subtitle.txt",
    "ios_promotional_text": "promotional_text.txt",
    "ios_keywords": "keywords.txt",
    "ios_description": "description.txt",
}


class PushError(RuntimeError):
    pass


def _require(cmd: str) -> None:
    if shutil.which(cmd) is None:
        raise PushError(f"'{cmd}' not found on PATH")


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise PushError(f"command failed ({result.returncode}): {' '.join(cmd)}")


def _android_locale(cfg: StudioConfig, lang: str) -> str:
    """Play Console locale for `lang` — its store_locales override, else `lang`
    as-is if it already looks like a Play locale (has a region, e.g. "en-US"),
    else the common `<lang>-<LANG>` guess Play expects for most languages
    (e.g. "de" -> "de-DE", "fr" -> "fr-FR"). "en" is special-cased to "en-US"
    since the naive `<lang>-<LANG>` guess produces "en-EN", which isn't a real
    Play locale — English requires an actual region (en-US/en-GB/...)."""
    if lang in cfg.store_locales:
        return cfg.store_locales[lang]
    if "-" in lang:
        return lang
    if lang == "en":
        return "en-US"
    return f"{lang}-{lang.upper()}"


def _ios_locale(cfg: StudioConfig, lang: str) -> str:
    """App Store Connect locale for `lang` — its store_locales override
    (only if it looks ASC-shaped, e.g. not Play's "el-GR"), else `lang` as-is.
    ASC's own locale list rarely matches Play's exactly (e.g. plain "el" vs
    Play's "el-GR"), so this intentionally does NOT reuse store_locales by
    default — set an ios-specific override in the config if you need one."""
    return lang


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
    """Writes each language's *proposed* Play listing copy (title/short/full
    description — from `store_listing.json`, see `generate_store_listing`/
    `save_proposed`) into fastlane/metadata/android/<locale>/ and uploads
    metadata only (no images/screenshots/APK) via `fastlane supply`."""
    _require("bundle")
    _require_android_creds(cfg.app)
    langs = langs or cfg.languages

    metadata_root = cfg.app.flutter_dir / "fastlane" / "metadata" / "android"
    for lang in langs:
        listing = load_listing(cfg, lang)
        locale_dir = metadata_root / _android_locale(cfg, lang)
        locale_dir.mkdir(parents=True, exist_ok=True)
        for field, filename in _PLAY_FIELDS.items():
            text = listing.get(field, {}).get("proposed", "")
            if text:
                (locale_dir / filename).write_text(text)

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
        ],
        cwd=cfg.app.flutter_dir,
    )


def push_ios_metadata(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Writes each language's *proposed* App Store Connect listing copy (name/
    subtitle/promotional text/keywords/description) into
    fastlane/metadata/ios/<locale>/ and uploads metadata only (no screenshots,
    no binary) via `fastlane deliver`.

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
    for lang in langs:
        listing = load_listing(cfg, lang)
        locale_dir = metadata_root / _ios_locale(cfg, lang)
        locale_dir.mkdir(parents=True, exist_ok=True)
        for field, filename in _IOS_FIELDS.items():
            text = listing.get(field, {}).get("proposed", "")
            if text:
                (locale_dir / filename).write_text(text)

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
        images_dir = metadata_root / _android_locale(cfg, lang) / "images"
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
        ],
        cwd=cfg.app.flutter_dir,
    )


def push_android_screenshots(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Uploads each language's composed screenshots (from `compose`) to Play
    Console — no metadata text, no icon/feature graphic. Devices whose config
    key contains "tablet" go to both sevenInchScreenshots and
    tenInchScreenshots (Play doesn't distinguish the two from a single
    source image); everything else goes to phoneScreenshots."""
    _require("bundle")
    _require_android_creds(cfg.app)
    langs = langs or cfg.languages

    metadata_root = cfg.app.flutter_dir / "fastlane" / "metadata" / "android"
    for lang in langs:
        locale_dir = metadata_root / _android_locale(cfg, lang) / "images"
        for device_key, device in cfg.devices.items():
            if device.kind != "android":
                continue
            src_dir = cfg.store_dir(lang) / device_key
            if not src_dir.is_dir():
                continue
            categories = (
                ["sevenInchScreenshots", "tenInchScreenshots"]
                if "tablet" in device_key
                else ["phoneScreenshots"]
            )
            for category in categories:
                dest_dir = locale_dir / category
                dest_dir.mkdir(parents=True, exist_ok=True)
                for i, src in enumerate(sorted(src_dir.glob("*.png")), start=1):
                    shutil.copyfile(src, dest_dir / f"{i}_{src.stem}.png")

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
        ],
        cwd=cfg.app.flutter_dir,
    )


def push_ios_screenshots(cfg: StudioConfig, langs: list[str] | None = None) -> None:
    """Uploads each language's composed screenshots (from `compose`) to App
    Store Connect — no metadata text, no binary. `deliver` auto-detects each
    image's device-size bucket from its pixel dimensions, so every iOS
    device's shots for a language are just dumped into one locale folder."""
    _require("bundle")
    _require_ios_creds(cfg.app)
    langs = langs or cfg.languages

    screenshots_root = cfg.app.flutter_dir / "fastlane" / "screenshots"
    for lang in langs:
        locale_dir = screenshots_root / _ios_locale(cfg, lang)
        if locale_dir.is_dir():
            shutil.rmtree(locale_dir)
        locale_dir.mkdir(parents=True, exist_ok=True)
        n = 1
        for device_key, device in cfg.devices.items():
            if device.kind != "ios":
                continue
            src_dir = cfg.store_dir(lang) / device_key
            if not src_dir.is_dir():
                continue
            for src in sorted(src_dir.glob("*.png")):
                shutil.copyfile(src, locale_dir / f"{n}_{device_key}_{src.stem}.png")
                n += 1

    api_key_path = _asc_api_key_file(cfg.app)
    try:
        _run(
            [
                "bundle", "exec", "fastlane", "deliver",
                "--app_identifier", cfg.app.bundle_id,
                "--api_key_path", str(api_key_path),
                "--screenshots_path", str(screenshots_root),
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
