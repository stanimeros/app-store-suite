from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("_", value.strip().lower()).strip("_")


@dataclass
class AppConfig:
    name: str
    flutter_dir: Path
    icon_source: Path


@dataclass
class DeviceConfig:
    key: str
    kind: str  # "ios" or "android"
    identifier: str  # simulator name (iOS) or AVD name (Android)


@dataclass
class StyleConfig:
    background_color: str = "#FAFAF8"
    title_color: str = "#1A1A1A"
    font_bold: str = "Poppins-Bold.ttf"
    font_regular: str = "Poppins-Regular.ttf"
    # "solid" always uses background_color. "auto" samples each raw screenshot's own
    # edge color per shot and lightens it toward white if needed for text contrast,
    # falling back to background_color only if that sampling fails.
    background_mode: str = "solid"


@dataclass
class StudioConfig:
    app: AppConfig
    devices: dict[str, DeviceConfig]
    style: StyleConfig
    config_path: Path
    languages: list[str] = field(default_factory=lambda: ["en"])

    @property
    def default_language(self) -> str:
        return self.languages[0]

    @property
    def output_dir(self) -> Path:
        """Per-app output root: output/<app-slug>/"""
        return self.config_path.parent.parent / "output" / _slugify(self.app.name)

    @property
    def raw_dir(self) -> Path:
        """Raw device captures — shared across languages (the UI text they show is
        whatever locale the device happened to be running in during capture)."""
        return self.output_dir / "raw"

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
        return self.lang_dir(lang) / "store_listing.md"


def load_config(path: str | Path) -> StudioConfig:
    path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(path.read_text())

    app_raw = raw["app"]
    flutter_dir = Path(app_raw["flutter_dir"]).expanduser()
    app = AppConfig(
        name=app_raw["name"],
        flutter_dir=flutter_dir,
        icon_source=flutter_dir / app_raw["icon_source"],
    )

    devices: dict[str, DeviceConfig] = {}
    for key, dev in raw["devices"].items():
        if "simulator" in dev:
            devices[key] = DeviceConfig(key=key, kind="ios", identifier=dev["simulator"])
        elif "avd" in dev:
            devices[key] = DeviceConfig(key=key, kind="android", identifier=dev["avd"])
        else:
            raise ValueError(f"Device '{key}' must define either 'simulator' or 'avd'")

    style_raw = raw.get("style", {})
    style = StyleConfig(
        background_color=style_raw.get("background_color", "#FAFAF8"),
        title_color=style_raw.get("title_color", "#1A1A1A"),
        font_bold=style_raw.get("font_bold", "Poppins-Bold.ttf"),
        font_regular=style_raw.get("font_regular", "Poppins-Regular.ttf"),
        background_mode=style_raw.get("background_mode", "solid"),
    )

    languages = raw.get("languages") or ["en"]

    return StudioConfig(
        app=app, devices=devices, style=style, config_path=path, languages=languages
    )
