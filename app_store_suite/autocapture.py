from __future__ import annotations

import time

from . import ai_titles, titles_store
from .capture.orchestrator import device_session
from .config import StudioConfig


class AutoCaptureError(RuntimeError):
    pass


def run_auto_capture(cfg: StudioConfig, only_device: str | None = None, render_delay: float = 2.0) -> None:
    """Unattended capture: for every configured shot, opens its deep link and
    screenshots the result — no navigation, no typing a shot name. Requires
    app.deep_link_scheme and a shots: list in the config (see README's
    "Auto-capture requirements" section for what the app itself must expose).
    """
    if not cfg.shots:
        raise AutoCaptureError(
            f"No shots: configured in {cfg.config_path} — add at least one {{id, route}} entry"
        )
    if not cfg.app.deep_link_scheme:
        raise AutoCaptureError(
            f"app.deep_link_scheme is not set in {cfg.config_path} — required for auto-capture"
        )

    devices = {only_device: cfg.devices[only_device]} if only_device else cfg.devices
    lang = cfg.default_language
    existing_titles = titles_store.load_titles(cfg, lang)

    for key, device in devices.items():
        print(f"\n=== {key} ({device.identifier}) ===")
        with device_session(cfg, key, device) as (take_screenshot, open_url):
            for shot in cfg.shots:
                url = cfg.deep_link(shot.route)
                print(f"[{key}] {shot.id}: opening {url}")
                open_url(url)
                time.sleep(render_delay)

                dest = cfg.raw_dir / key / f"{shot.id}.png"
                take_screenshot(dest)
                print(f"  saved {dest}")

                if shot.id in existing_titles:
                    continue
                try:
                    print("  asking claude for a title...")
                    suggestion = ai_titles.suggest_title(dest, cfg.app.name, lang=lang)
                    titles_store.save_title(
                        cfg, lang, shot.id, suggestion["title"], suggestion.get("subtitle", "")
                    )
                    existing_titles[shot.id] = suggestion
                    print(f"  title: {suggestion['title']!r}  subtitle: {suggestion.get('subtitle', '')!r}")
                except ai_titles.TitleSuggestionError as exc:
                    print(f"  WARNING: title suggestion failed ({exc}); edit {cfg.titles_path(lang)} manually")
