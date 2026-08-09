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
    # URL scheme the app registers for debug deep links, e.g. "chronal" for
    # chronal://<route>. Required only for `auto-capture` — see ShotConfig.
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


@dataclass
class DeviceConfig:
    key: str
    kind: str  # "ios" or "android"
    identifier: str  # simulator name (iOS) or AVD name (Android)


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


@dataclass
class AutoCaptureConfig:
    """Timing knobs for `auto-capture` — overridable per run via CLI flags."""

    # Seconds to wait after `flutter run` reports ready, before the first deep link.
    # Covers cold-start splash, Firebase/content bootstrap, etc.
    warmup_delay: float = 0.0
    render_delay: float = 6.0


@dataclass
class StyleConfig:
    background_color: str = "#FAFAF8"
    title_color: str = "#1A1A1A"
    font_bold: str = "Poppins-Bold.ttf"
    font_regular: str = "Poppins-Regular.ttf"
    # "solid" always uses background_color. "auto" samples each raw screenshot's own
    # edge color per shot and lightens it toward white if needed for text contrast,
    # falling back to background_color only if that sampling fails. "gradient" blends
    # background_color -> gradient_color2 top to bottom; if gradient_color2 is unset,
    # it's auto-derived per shot from a vivid accent color sampled from that
    # screenshot's own content (not its white/gray UI chrome), pastel-lightened.
    background_mode: str = "solid"
    gradient_color2: str | None = None
    # "centered": device sits upright, bottom-anchored, centered. "tilted": device is
    # rotated by tilt_degrees, alternating left/right per shot (deterministic by shot id).
    layout: str = "centered"
    tilt_degrees: float = 6.0
    # "none", "shapes" (soft translucent circles drawn behind the device, no extra
    # dependency), or "svg" (rasterizes a .svg file from decoration_svg_dir behind the
    # device via CairoSVG, picked deterministically per shot id).
    decoration: str = "none"
    # If unset, auto-derived per shot the same way as gradient_color2 (falls back to
    # title_color if the screenshot has no sufficiently vivid content to sample).
    decoration_color: str | None = None
    decoration_svg_dir: Path | None = None


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
        """Output root, next to the config itself (which lives in the app's own repo,
        e.g. alongside pubspec.yaml/l10n.yaml — not inside app-store-suite)."""
        return self.config_path.parent / ".appstoresuite"

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

    def store_dir(self, lang: str) -> Path:
        return self.lang_dir(lang) / "store"

    def titles_path(self, lang: str) -> Path:
        return self.lang_dir(lang) / "titles.json"

    def feature_graphic_path(self, lang: str) -> Path:
        return self.lang_dir(lang) / "feature_graphic.png"

    def store_listing_path(self, lang: str) -> Path:
        return self.lang_dir(lang) / "store_listing.json"


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
    decoration_svg_dir_raw = style_raw.get("decoration_svg_dir")
    style = StyleConfig(
        background_color=style_raw.get("background_color", "#FAFAF8"),
        title_color=style_raw.get("title_color", "#1A1A1A"),
        font_bold=style_raw.get("font_bold", "Poppins-Bold.ttf"),
        font_regular=style_raw.get("font_regular", "Poppins-Regular.ttf"),
        background_mode=style_raw.get("background_mode", "solid"),
        gradient_color2=style_raw.get("gradient_color2"),
        layout=style_raw.get("layout", "centered"),
        tilt_degrees=float(style_raw.get("tilt_degrees", 6.0)),
        decoration=style_raw.get("decoration", "none"),
        decoration_color=style_raw.get("decoration_color"),
        decoration_svg_dir=(path.parent / decoration_svg_dir_raw) if decoration_svg_dir_raw else None,
    )

    languages = raw.get("languages") or ["en"]

    shots = []
    for i, shot_raw in enumerate(raw.get("shots") or []):
        if not isinstance(shot_raw, dict):
            raise ConfigError(f"{path}: shots[{i}] must be a mapping with 'id' and 'route'")
        shots.append(
            ShotConfig(
                id=_require_key(shot_raw, "id", where=f"shots[{i}]", config_path=path),
                route=_require_key(shot_raw, "route", where=f"shots[{i}]", config_path=path),
            )
        )

    store_locales = raw.get("store_locales") or {}

    ac_raw = raw.get("auto_capture") or {}
    if not isinstance(ac_raw, dict):
        raise ConfigError(f"{path}: 'auto_capture' must be a mapping")
    auto_capture = AutoCaptureConfig(
        warmup_delay=float(ac_raw.get("warmup_delay", 0)),
        render_delay=float(ac_raw.get("render_delay", 6.0)),
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
