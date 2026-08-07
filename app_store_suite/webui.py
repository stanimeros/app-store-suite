from __future__ import annotations

import os
import random
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from . import ship, shots, store_listing, style_choices, titles_store
from .autocapture import run_auto_capture
from .compose import compose_all
from .config import StudioConfig
from .store_listing import generate_store_listing
from .style_variants import VARIANTS

STATIC_DIR = Path(__file__).parent / "webui_static"


class Studio:
    def __init__(self, cfg: StudioConfig):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.current_lang = cfg.default_language
        self.busy = False
        self.status = "Idle."
        self.error: str | None = None

    def _set_status(self, text: str) -> None:
        with self.lock:
            self.status = text

    def set_language(self, lang: str) -> None:
        if lang not in self.cfg.languages:
            raise ValueError(f"Unknown language '{lang}'; configured: {self.cfg.languages}")
        with self.lock:
            self.current_lang = lang

    def snapshot(self) -> dict:
        with self.lock:
            lang = self.current_lang
            shot_ids = shots.discover_shot_ids(self.cfg)
            titles = titles_store.load_titles(self.cfg, lang)
            rows = []
            for sid in shot_ids:
                meta = titles.get(sid, {})
                captured = {
                    key: sid in shots.captured_shot_ids(self.cfg, key) for key in self.cfg.devices
                }
                composed = {
                    key: (self.cfg.store_dir(lang) / key / f"{sid}.png").exists() for key in self.cfg.devices
                }
                rows.append(
                    {
                        "id": sid,
                        "title": meta.get("title", ""),
                        "subtitle": meta.get("subtitle", ""),
                        "title_pending": sid not in titles,
                        "captured": captured,
                        "composed": composed,
                        "style_variant": style_choices.load_choices(self.cfg).get(sid),
                    }
                )
            return {
                "app_name": self.cfg.app.name,
                "languages": self.cfg.languages,
                "current_lang": lang,
                "status": self.status,
                "busy": self.busy,
                "error": self.error,
                "devices": [
                    {"key": key, "identifier": d.identifier, "kind": d.kind}
                    for key, d in self.cfg.devices.items()
                ],
                "configured_shots": [s.id for s in self.cfg.shots],
                "shots": rows,
                "style_variants": list(VARIANTS),
            }

    def auto_capture(self, device_key: str | None) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.status = (
                f"Auto-capturing on {device_key}..." if device_key else "Auto-capturing on all devices..."
            )
        threading.Thread(target=self._auto_capture_worker, args=(device_key,), daemon=True).start()

    def _auto_capture_worker(self, device_key: str | None) -> None:
        try:
            run_auto_capture(self.cfg, only_device=device_key)
            with self.lock:
                self.status = "Auto-capture complete."
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def compose(self) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            lang = self.current_lang
            self.status = f"Composing store screenshots ({lang})..."
        threading.Thread(target=self._compose_worker, args=(lang,), daemon=True).start()

    def _compose_worker(self, lang: str) -> None:
        try:
            outputs = compose_all(self.cfg, lang)
            with self.lock:
                self.status = f"Composed {len(outputs)} image(s) into {self.cfg.store_dir(lang)}"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def randomize_style(self) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            lang = self.current_lang
            self.status = "Picking random styles..."
        threading.Thread(target=self._randomize_style_worker, args=(lang,), daemon=True).start()

    def _randomize_style_worker(self, lang: str) -> None:
        try:
            for shot_id in shots.discover_shot_ids(self.cfg):
                style_choices.save_choice(self.cfg, shot_id, random.choice(list(VARIANTS)))
            with self.lock:
                self.status = "Recomposing with new styles..."
            outputs = compose_all(self.cfg, lang)
            with self.lock:
                self.status = f"Recomposed {len(outputs)} image(s) with random styles"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def set_style(self, shot_id: str, variant: str | None) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            lang = self.current_lang
            self.status = f"Setting style for '{shot_id}'..."
        threading.Thread(target=self._set_style_worker, args=(lang, shot_id, variant), daemon=True).start()

    def _set_style_worker(self, lang: str, shot_id: str, variant: str | None) -> None:
        try:
            if variant:
                style_choices.save_choice(self.cfg, shot_id, variant)
            else:
                style_choices.clear_choice(self.cfg, shot_id)
            outputs = compose_all(self.cfg, lang, only_device=None)
            with self.lock:
                self.status = f"Recomposed {len(outputs)} image(s)"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def set_style_all(self, variant: str) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            lang = self.current_lang
            self.status = f"Applying '{variant}' style to every shot..."
        threading.Thread(target=self._set_style_all_worker, args=(lang, variant), daemon=True).start()

    def _set_style_all_worker(self, lang: str, variant: str) -> None:
        try:
            for shot_id in shots.discover_shot_ids(self.cfg):
                style_choices.save_choice(self.cfg, shot_id, variant)
            with self.lock:
                self.status = "Recomposing..."
            outputs = compose_all(self.cfg, lang)
            with self.lock:
                self.status = f"Recomposed {len(outputs)} image(s) with '{variant}' style"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def get_store_listing(self, lang: str) -> dict:
        return store_listing.load_listing(self.cfg, lang)

    def save_store_listing(self, lang: str, proposed: dict[str, str]) -> None:
        store_listing.save_proposed(self.cfg, lang, proposed)
        self._set_status(f"Saved proposed listing copy ({lang})")

    def generate_listing(self, lang: str) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.status = f"Asking claude for proposed listing copy ({lang})..."
        threading.Thread(target=self._generate_listing_worker, args=(lang,), daemon=True).start()

    def _generate_listing_worker(self, lang: str) -> None:
        try:
            dest = generate_store_listing(self.cfg, lang)
            with self.lock:
                self.status = f"Proposed listing copy written to {dest}"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def fetch_listing(self, lang: str) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.status = f"Fetching current listing copy from the stores ({lang})..."
        threading.Thread(target=self._fetch_listing_worker, args=(lang,), daemon=True).start()

    def _fetch_listing_worker(self, lang: str) -> None:
        try:
            dest = store_listing.fetch_current_listing(self.cfg, lang)
            with self.lock:
                self.status = f"Current listing copy fetched into {dest}"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def ship(self, kind: str) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.status = f"Shipping ({kind})... this can take a while"
        threading.Thread(target=self._ship_worker, args=(kind,), daemon=True).start()

    def _ship_worker(self, kind: str) -> None:
        try:
            flutter_dir = self.cfg.app.flutter_dir
            if kind == "ios":
                ship.ship_ios(flutter_dir)
            elif kind == "android":
                ship.ship_android(flutter_dir)
            else:
                raise ValueError(f"Unknown ship kind '{kind}'")
            with self.lock:
                self.status = f"Shipped ({kind})"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def delete_shot(self, shot_id: str) -> None:
        with self.lock:
            lang = self.current_lang
            for key in self.cfg.devices:
                for base_dir in (self.cfg.raw_dir, self.cfg.store_dir(lang)):
                    path = base_dir / key / f"{shot_id}.png"
                    if path.exists():
                        path.unlink()
            titles_store.delete_title(self.cfg, lang, shot_id)
            self.status = f"Deleted '{shot_id}'"


def create_app(cfg: StudioConfig) -> Flask:
    studio = Studio(cfg)
    app = Flask(__name__, static_folder=None)
    app.studio = studio  # type: ignore[attr-defined]

    @app.get("/")
    def index():
        return send_file(STATIC_DIR / "index.html")

    @app.get("/api/state")
    def state():
        return jsonify(studio.snapshot())

    @app.post("/api/select-language")
    def select_language():
        try:
            studio.set_language(request.json["lang"])
            return jsonify({"ok": True})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/auto-capture")
    def auto_capture_route():
        studio.auto_capture(request.json.get("device"))
        return jsonify({"ok": True})

    @app.post("/api/delete-shot")
    def delete_shot_route():
        studio.delete_shot(request.json["shot_id"])
        return jsonify({"ok": True})

    @app.post("/api/compose")
    def compose_route():
        studio.compose()
        return jsonify({"ok": True})

    @app.get("/api/raw/<device>/<shot_id>.png")
    def raw_image(device: str, shot_id: str):
        path = cfg.raw_dir / device / f"{shot_id}.png"
        if not path.exists():
            return "", 404
        return send_file(path, mimetype="image/png")

    @app.get("/api/store/<lang>/<device>/<shot_id>.png")
    def store_image(lang: str, device: str, shot_id: str):
        path = cfg.store_dir(lang) / device / f"{shot_id}.png"
        if not path.exists():
            return "", 404
        return send_file(path, mimetype="image/png")

    @app.post("/api/randomize-style")
    def randomize_style_route():
        studio.randomize_style()
        return jsonify({"ok": True})

    @app.post("/api/set-style")
    def set_style_route():
        studio.set_style(request.json["shot_id"], request.json.get("variant") or None)
        return jsonify({"ok": True})

    @app.post("/api/set-style-all")
    def set_style_all_route():
        studio.set_style_all(request.json["variant"])
        return jsonify({"ok": True})

    @app.get("/api/store-listing")
    def get_store_listing_route():
        lang = request.args.get("lang", studio.current_lang)
        return jsonify({"lang": lang, "fields": studio.get_store_listing(lang)})

    @app.post("/api/store-listing")
    def save_store_listing_route():
        data = request.json
        studio.save_store_listing(data["lang"], data["proposed"])
        return jsonify({"ok": True})

    @app.post("/api/generate-store-listing")
    def generate_store_listing_route():
        studio.generate_listing(request.json.get("lang") or studio.current_lang)
        return jsonify({"ok": True})

    @app.post("/api/fetch-listing")
    def fetch_listing_route():
        studio.fetch_listing(request.json.get("lang") or studio.current_lang)
        return jsonify({"ok": True})

    @app.post("/api/ship")
    def ship_route():
        studio.ship(request.json["kind"])
        return jsonify({"ok": True})

    @app.post("/api/quit")
    def quit_route():
        def _exit_soon():
            time.sleep(0.3)
            os._exit(0)

        threading.Thread(target=_exit_soon, daemon=True).start()
        return jsonify({"ok": True})

    return app


def launch_web_ui(cfg: StudioConfig, port: int = 5175) -> None:
    app = create_app(cfg)
    url = f"http://127.0.0.1:{port}"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"app-store-suite UI running at {url} (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
