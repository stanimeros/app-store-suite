from __future__ import annotations

import time

from . import ai_titles, titles_store
from .capture.orchestrator import device_session
from .config import StudioConfig


class AutoCaptureError(RuntimeError):
    pass


def run_auto_capture(
    cfg: StudioConfig,
    only_device: str | None = None,
    render_delay: float = 6.0,
    lang: str | None = None,
) -> None:
    """Unattended capture: for every configured shot, opens its deep link and
    screenshots the result — no navigation, no typing a shot name. Requires
    app.deep_link_scheme and a shots: list in the config (see README's
    "Auto-capture requirements" section for what the app itself must expose).

    Pass `lang` for apps that support switching the in-app language via a
    `lang` deep-link query param (the app's own debug router decides what to
    do with it — see README's "Auto-capture requirements"): it's appended to
    every shot's URL as `?lang=<lang>` (or `&lang=<lang>` if the route already
    has a query string), raw screenshots are saved under `raw/<lang>/<device>/`
    instead of the shared `raw/<device>/`, and suggested shot titles are
    saved/read against that same language instead of always `cfg.default_language`.

    `lang` may be a comma-separated list (e.g. `"en,el"`) to capture multiple
    languages per device without rebooting/relaunching in between — each
    device is only booted once, and every configured language's shots run
    back-to-back inside that same app session before moving to the next
    device. This also means any iOS "Open in <App>?" dialog only has to be
    dismissed once per device for the whole run, not once per language.
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
    langs: list[str | None] = [s.strip() for s in lang.split(",")] if lang else [None]

    for key, device in devices.items():
        print(f"\n=== {key} ({device.identifier}) ===")
        with device_session(cfg, key, device) as (take_screenshot, open_url):
            for one_lang in langs:
                titles_lang = one_lang or cfg.default_language
                existing_titles = titles_store.load_titles(cfg, titles_lang)
                raw_base = cfg.raw_dir_for(one_lang)

                for shot in cfg.shots:
                    route = shot.route
                    if one_lang is not None:
                        sep = "&" if "?" in route else "?"
                        route = f"{route}{sep}lang={one_lang}"
                    url = cfg.deep_link(route)
                    label = f"{one_lang}/{shot.id}" if one_lang else shot.id
                    print(f"[{key}] {label}: opening {url}")
                    open_url(url)
                    time.sleep(render_delay)

                    dest = raw_base / key / f"{shot.id}.png"
                    take_screenshot(dest)
                    print(f"  saved {dest}")

                    if shot.id in existing_titles:
                        continue
                    try:
                        print("  asking claude for a title...")
                        suggestion = ai_titles.suggest_title(dest, cfg.app.name, lang=titles_lang)
                        titles_store.save_title(
                            cfg, titles_lang, shot.id, suggestion["title"], suggestion.get("subtitle", "")
                        )
                        existing_titles[shot.id] = suggestion
                        print(f"  title: {suggestion['title']!r}  subtitle: {suggestion.get('subtitle', '')!r}")
                    except ai_titles.TitleSuggestionError as exc:
                        print(f"  WARNING: title suggestion failed ({exc}); edit {cfg.titles_path(titles_lang)} manually")
