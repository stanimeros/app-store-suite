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

    return StudioConfig(
        app=app, devices=devices, style=style, config_path=path, languages=languages
    )
