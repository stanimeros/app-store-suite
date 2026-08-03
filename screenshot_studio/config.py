from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


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


@dataclass
class StudioConfig:
    app: AppConfig
    devices: dict[str, DeviceConfig]
    style: StyleConfig
    config_path: Path

    @property
    def output_dir(self) -> Path:
        return self.config_path.parent.parent / "output"

    @property
    def raw_dir(self) -> Path:
        return self.output_dir / "raw"

    @property
    def store_dir(self) -> Path:
        return self.output_dir / "store"

    @property
    def titles_path(self) -> Path:
        return self.output_dir / "titles.json"


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
    )

    return StudioConfig(app=app, devices=devices, style=style, config_path=path)
