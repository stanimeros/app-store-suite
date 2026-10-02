from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised for anything wrong with app_store_suite.yaml itself (missing file,
    invalid YAML, missing/malformed keys) — always includes the config path and
    the offending key so agents/users can fix it without reading this module."""


@dataclass
class AppConfig:
    name: str
    flutter_dir: Path
    icon_source: Path
    # URL scheme the app registers for debug deep links, e.g. "myapp" for
    # myapp://<route>. Required only for `auto-capture` — see ShotConfig.
    deep_link_scheme: str | None = None

    # Store identifiers + credentials, required only for `fetch-listing` (pulling
    # the currently-live listing copy from App Store Connect / Play Console).
    # Reuse whatever credentials the app's own Fastfile already uses for shipping.
    bundle_id: str | None = None  # iOS app_identifier
    asc_key_id: str | None = None
    asc_issuer_id: str | None = None
    asc_key_path: Path | None = None  # .p8 file
    android_package_name: str | None = None
    play_json_key: Path | None = None  # service account json
    # How the feature graphic draws icon_source. "cutout" (default) keys out
    # the icon's solid background so only the badge shape remains - right for
    # a logo on a flat fill. "rounded" keeps the whole icon as a rounded
    # square, the way it looks on a home screen - right for an icon whose
    # solid background *is* part of the design (e.g. a white card).
    icon_shape: str = "cutout"


@dataclass
class DeviceConfig:
    key: str
    kind: str  # "ios" or "android"
    identifier: str  # simulator name (iOS) or AVD name (Android)


TEXT_COLOR_MODES = ("auto", "exact")
OVERLAY_ANCHORS = ("top-left", "top-right", "bottom-left", "bottom-right", "device-top")


@dataclass
class OverlayConfig:
    """An image (e.g. the app's mascot, transparent background) that `compose`
    draws on top of a shot's store image.

    `anchor` picks the reference point; `offset_x`/`offset_y` (fractions of the
    canvas width, +x right, +y down) nudge it from there:
    - `top-left` / `top-right` / `bottom-left` / `bottom-right`: that corner
      of the canvas (the art's matching corner sits on it, inset by a small
      margin).
    - `device-top`: centered on the device frame's top edge, the art's bottom
      on that edge; with `behind: true` it "peeks" over the frame, and the
      lower `1 - visible` of the art is hidden behind the device (the device
      moves down to make room, so it never covers the title).

    `size` (and the offsets) are fractions of the canvas width — of a
    phone-shaped canvas: on wider tablet canvases they're measured against the
    width a phone canvas of the same height would have, so the art keeps the
    same scale relative to the device everywhere. `rotation` in
    degrees (counter-clockwise), `behind` draws it under the device instead of
    over it. `image` is resolved relative to the app's flutter_dir.
    """

    image: Path
    anchor: str = "bottom-right"
    size: float = 0.34
    rotation: float = 0.0
    flip: bool = False
    behind: bool = False
    offset_x: float = 0.0
    offset_y: float = 0.0
    visible: float = 0.8


@dataclass
class ShotConfig:
    """A fixed, named screen the app exposes for unattended capture.

    `route` is opened as `<app.deep_link_scheme>://<route>` — the app's own debug
    router is responsible for landing on the right screen with sample/mock data
    already loaded (no login, no live network state). See README's "Auto-capture
    requirements" section for the full contract.
    """

    id: str
    route: str
    overlays: list[OverlayConfig] = field(default_factory=list)
    # Appended to `generate-bgs`'s prompt after `style.background_guide`, for
    # this shot only - lets each shot's background keep the shared style but
    # get its own color/character, so a set doesn't read as one image four
    # times.
    background_guide: str | None = None


@dataclass
class AutoCaptureConfig:
    """Timing knobs for `auto-capture` — overridable per run via CLI flags."""

    # Seconds to wait after `flutter run` reports ready, before the first deep link.
    # Covers cold-start splash, Firebase/content bootstrap, etc.
    warmup_delay: float = 0.0
    render_delay: float = 6.0
    # iOS only. When set (e.g. "appstoresuite_route"), routes are delivered by
    # writing the deep link into this file in the app's tmp/ dir instead of
    # `simctl openurl` — which shows an "Open in <App>?" sheet on every link
    # that has to be tapped by hand (and can't be when the Simulator GUI isn't
    # scriptable, e.g. Xcode 27's DeviceHub). The app's debug router must poll
    # the file; see README "Auto-capture requirements". Needs app.bundle_id.
    ios_route_file: str | None = None


@dataclass
class StyleConfig:
    background_color: str = "#FAFAF8"
    title_color: str = "#1A1A1A"
    # Defaults to title_color when unset — override only if the subtitle should
    # read as a visually distinct (usually lighter/muted) color from the title.
    subtitle_color: str | None = None
    font_bold: str = "Poppins-Bold.ttf"
    font_regular: str = "Poppins-Regular.ttf"
    # Extra art direction appended to `generate-bgs`'s prompt (e.g. "stylized
    # snowy mountains in the app's blue"); the raw screenshot is still the
    # color/mood reference.
    background_guide: str | None = None
    # "auto": title/subtitle colors adapt to the background they sit on (ink
    # colors become a tint of the background's hue). "exact": always use
    # title_color/subtitle_color as given (you've checked they contrast).
    text_color_mode: str = "auto"
    # "centered": device sits upright, bottom-anchored, centered. "tilted": device is
    # rotated by tilt_degrees, alternating left/right per shot (deterministic by shot id).
    layout: str = "centered"
    tilt_degrees: float = 6.0


@dataclass
class StudioConfig:
    app: AppConfig
    devices: dict[str, DeviceConfig]
    style: StyleConfig
    config_path: Path
    languages: list[str] = field(default_factory=lambda: ["en"])
    shots: list[ShotConfig] = field(default_factory=list)
    auto_capture: AutoCaptureConfig = field(default_factory=AutoCaptureConfig)
    # Maps our language code -> store-specific locale code, e.g. {"en": "en-US"}.
    # Only needed where they differ; unmapped languages are tried as-is first, then
    # against a few common variants (see store_listing.py's _resolve_locale).
    store_locales: dict[str, str] = field(default_factory=dict)

    def deep_link(self, route: str) -> str:
        if not self.app.deep_link_scheme:
            raise ValueError(
                f"app.deep_link_scheme is not set in {self.config_path} — required for auto-capture"
            )
        return f"{self.app.deep_link_scheme}://{route}"

    @property
    def default_language(self) -> str:
        return self.languages[0]

    @property
    def output_dir(self) -> Path:
        """Tool bookkeeping (raw captures, style choices, titles) — kept inside the
        app's own fastlane/ dir, visible (not dot-prefixed) since fastlane/ is already
        working-tree scratch space, not something browsed casually."""
        return self.app.flutter_dir / "fastlane" / "appstoresuite"

    @property
    def raw_dir(self) -> Path:
        """Raw device captures — shared across languages (the UI text they show is
        whatever locale the device happened to be running in during capture)."""
        return self.output_dir / "raw"

    def raw_dir_for(self, lang: str | None) -> Path:
        """Language-scoped raw captures, for apps whose deep-link routes switch the
        in-app language per capture (e.g. `?lang=el`) — each language's raw shots are
        kept apart under `raw/<lang>/` instead of overwriting the shared `raw_dir`.
        Falls back to the plain `raw_dir` when `lang` is None."""
        return self.raw_dir if lang is None else self.raw_dir / lang

    @property
    def icon_path(self) -> Path:
        """Play Store app icon — no text rendered on it, so it isn't per-language."""
        return self.output_dir / "play_store_icon.png"

    def lang_dir(self, lang: str) -> Path:
        return self.output_dir / lang

    def titles_path(self, lang: str) -> Path:
        return self.lang_dir(lang) / "titles.json"

    def feature_graphic_path(self, lang: str) -> Path:
        return self.lang_dir(lang) / "feature_graphic.png"

    # --- Store-facing locale codes ------------------------------------------------

    def android_locale(self, lang: str) -> str:
        """Play Console locale for `lang` — its store_locales override, else `lang`
        as-is if it already looks like a Play locale (has a region, e.g. "en-US"),
        else the common `<lang>-<LANG>` guess Play expects for most languages (e.g.
        "de" -> "de-DE", "fr" -> "fr-FR"). "en" is special-cased to "en-US" since the
        naive `<lang>-<LANG>` guess produces "en-EN", which isn't a real Play locale —
        English requires an actual region (en-US/en-GB/...)."""
        if lang in self.store_locales:
            return self.store_locales[lang]
        if "-" in lang:
            return lang
        if lang == "en":
            return "en-US"
        return f"{lang}-{lang.upper()}"

    def ios_locale(self, lang: str) -> str:
        """App Store Connect locale for `lang` — `lang` as-is, except "en" which ASC
        requires a real region for (no bare "en" in its locale list, unlike e.g. bare
        "el" which is valid) — defaults to "en-US". This intentionally does NOT reuse
        `store_locales` (that's Play-shaped, e.g. Play's "el-GR" vs ASC's plain "el") —
        there's no ASC-specific override in the config yet, so other ambiguous codes
        (pt, zh, ...) need a manual rename of the fastlane/metadata/ios/<locale> and
        fastlane/screenshots/<locale> directories to whatever ASC expects."""
        if lang == "en":
            return "en-US"
        return lang

    # --- Real fastlane output paths (compose/store-listing/fetch-listing write
    # directly here; push just uploads what's already in place) --------------------

    def ios_screenshots_dir(self, lang: str) -> Path:
        return self.app.flutter_dir / "fastlane" / "screenshots" / self.ios_locale(lang)

    def android_images_dir(self, lang: str) -> Path:
        return self.android_metadata_dir(lang) / "images"

    def ios_metadata_dir(self, lang: str) -> Path:
        return self.app.flutter_dir / "fastlane" / "metadata" / "ios" / self.ios_locale(lang)

    def android_metadata_dir(self, lang: str) -> Path:
        return self.app.flutter_dir / "fastlane" / "metadata" / "android" / self.android_locale(lang)


def parse_dotenv(path: Path) -> dict[str, str]:
    """Parses a simple `.env` file of `KEY=value` lines. Blank lines, comment lines
    (starting with '#'), and lines without '=' are skipped; an optional 'export '
    prefix on the key is stripped; values are unquoted if wrapped in matching quotes.
    Returns {} if `path` doesn't exist. Shared by ship.translate_arb (bulk-loads every
    key) and backgrounds._env_value (looks up one key) so the parsing rules stay in
    one place."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def _require_key(d: dict, key: str, *, where: str, config_path: Path) -> Any:
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{config_path}: missing required key '{key}' under {where}")
    return d[key]


def load_config(path: str | Path) -> StudioConfig:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")

    if not isinstance(raw.get("app"), dict):
        raise ConfigError(f"{path}: missing required top-level key 'app'")
    app_raw = raw["app"]

    flutter_dir = Path(app_raw.get("flutter_dir", ".")).expanduser()
    if not flutter_dir.is_absolute():
        flutter_dir = (path.parent / flutter_dir).resolve()

    def _resolve(key: str) -> Path | None:
        raw_value = app_raw.get(key)
        if not raw_value:
            return None
        value = Path(raw_value).expanduser()
        return value if value.is_absolute() else (flutter_dir / value).resolve()

    app = AppConfig(
        name=_require_key(app_raw, "name", where="app", config_path=path),
        flutter_dir=flutter_dir,
        icon_source=flutter_dir / _require_key(app_raw, "icon_source", where="app", config_path=path),
        icon_shape=app_raw.get("icon_shape", "cutout"),
        deep_link_scheme=app_raw.get("deep_link_scheme"),
        bundle_id=app_raw.get("bundle_id"),
        asc_key_id=app_raw.get("asc_key_id"),
        asc_issuer_id=app_raw.get("asc_issuer_id"),
        asc_key_path=_resolve("asc_key_path"),
        android_package_name=app_raw.get("android_package_name"),
        play_json_key=_resolve("play_json_key"),
    )

    if not isinstance(raw.get("devices"), dict) or not raw["devices"]:
        raise ConfigError(f"{path}: missing or empty required top-level key 'devices'")

    devices: dict[str, DeviceConfig] = {}
    for key, dev in raw["devices"].items():
        if not isinstance(dev, dict):
            raise ConfigError(f"{path}: devices.{key} must be a mapping with 'simulator' or 'avd'")
        if "simulator" in dev:
            devices[key] = DeviceConfig(key=key, kind="ios", identifier=dev["simulator"])
        elif "avd" in dev:
            devices[key] = DeviceConfig(key=key, kind="android", identifier=dev["avd"])
        else:
            raise ConfigError(f"{path}: devices.{key} must define either 'simulator' or 'avd'")

    style_raw = raw.get("style", {})
    if not isinstance(style_raw, dict):
        raise ConfigError(f"{path}: 'style' must be a mapping")
    def font(key: str, default: str) -> str:
        # A bare file name is a bundled font; anything with a path separator
        # is the app's own font file, relative to flutter_dir.
        value = style_raw.get(key, default)
        if "/" in value:
            font_file = Path(value).expanduser()
            if not font_file.is_absolute():
                font_file = (flutter_dir / font_file).resolve()
            if not font_file.is_file():
                raise ConfigError(f"{path}: style.{key} font not found: {font_file}")
            return str(font_file)
        return value

    text_color_mode = style_raw.get("text_color_mode", "auto")
    if text_color_mode not in TEXT_COLOR_MODES:
        raise ConfigError(f"{path}: style.text_color_mode must be one of {', '.join(TEXT_COLOR_MODES)}")

    style = StyleConfig(
        background_color=style_raw.get("background_color", "#FAFAF8"),
        title_color=style_raw.get("title_color", "#1A1A1A"),
        subtitle_color=style_raw.get("subtitle_color"),
        font_bold=font("font_bold", "Poppins-Bold.ttf"),
        font_regular=font("font_regular", "Poppins-Regular.ttf"),
        background_guide=style_raw.get("background_guide") or None,
        text_color_mode=text_color_mode,
        layout=style_raw.get("layout", "centered"),
        tilt_degrees=float(style_raw.get("tilt_degrees", 6.0)),
    )

    languages = raw.get("languages") or ["en"]

    shots = []
    for i, shot_raw in enumerate(raw.get("shots") or []):
        if not isinstance(shot_raw, dict):
            raise ConfigError(f"{path}: shots[{i}] must be a mapping with 'id' and 'route'")
        overlays = []
        for j, ov in enumerate(shot_raw.get("overlays") or []):
            where = f"shots[{i}].overlays[{j}]"
            if not isinstance(ov, dict) or "image" not in ov:
                raise ConfigError(f"{path}: {where} must be a mapping with at least 'image'")
            anchor = ov.get("anchor", "bottom-right")
            if anchor not in OVERLAY_ANCHORS:
                raise ConfigError(f"{path}: {where}.anchor must be one of {', '.join(OVERLAY_ANCHORS)}")
            size = float(ov.get("size", 0.34))
            if size <= 0:
                raise ConfigError(f"{path}: {where}.size must be greater than 0")
            visible = float(ov.get("visible", 0.8))
            if not 0 <= visible <= 1:
                raise ConfigError(f"{path}: {where}.visible must be between 0 and 1")
            image = Path(ov["image"]).expanduser()
            if not image.is_absolute():
                image = (flutter_dir / image).resolve()
            overlays.append(
                OverlayConfig(
                    image=image,
                    anchor=anchor,
                    size=size,
                    rotation=float(ov.get("rotation", 0)),
                    flip=bool(ov.get("flip", False)),
                    behind=bool(ov.get("behind", anchor == "device-top")),
                    offset_x=float(ov.get("offset_x", 0)),
                    offset_y=float(ov.get("offset_y", 0)),
                    visible=visible,
                )
            )
        shots.append(
            ShotConfig(
                id=_require_key(shot_raw, "id", where=f"shots[{i}]", config_path=path),
                route=_require_key(shot_raw, "route", where=f"shots[{i}]", config_path=path),
                overlays=overlays,
                background_guide=shot_raw.get("background_guide"),
            )
        )

    store_locales = raw.get("store_locales") or {}

    ac_raw = raw.get("auto_capture") or {}
    if not isinstance(ac_raw, dict):
        raise ConfigError(f"{path}: 'auto_capture' must be a mapping")
    auto_capture = AutoCaptureConfig(
        warmup_delay=float(ac_raw.get("warmup_delay", 0)),
        render_delay=float(ac_raw.get("render_delay", 6.0)),
        ios_route_file=ac_raw.get("ios_route_file") or None,
    )

    return StudioConfig(
        app=app,
        devices=devices,
        style=style,
        config_path=path,
        languages=languages,
        shots=shots,
        store_locales=store_locales,
        auto_capture=auto_capture,
    )
