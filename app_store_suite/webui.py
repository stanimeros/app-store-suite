from __future__ import annotations

import os
import re
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from . import ai_titles, shots, titles_store
from .capture import android, flutter_app, ios
from .compose import compose_all
from .config import DeviceConfig, StudioConfig

STATIC_DIR = Path(__file__).parent / "webui_static"
_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _slugify(raw: str) -> str:
    slug = _SLUG_RE.sub("_", raw.strip().lower()).strip("_")
    return slug


class DeviceSession:
    def __init__(self, key: str, device: DeviceConfig):
        self.key = key
        self.device = device
        self.identifier: str | None = None
        self.we_booted = False
        self.flutter_proc = None
        self.app_running = False


class Studio:
    def __init__(self, cfg: StudioConfig):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.sessions = {key: DeviceSession(key, device) for key, device in cfg.devices.items()}
        self.active_key: str | None = None
        self.current_lang = cfg.default_language
        self.busy = False
        self.status = "Select a device to begin."
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
                    key: sid in shots.captured_shot_ids(self.cfg, key) for key in self.sessions
                }
                rows.append(
                    {
                        "id": sid,
                        "title": meta.get("title", ""),
                        "subtitle": meta.get("subtitle", ""),
                        "title_pending": sid not in titles,
                        "captured": captured,
                    }
                )
            return {
                "app_name": self.cfg.app.name,
                "languages": self.cfg.languages,
                "current_lang": lang,
                "status": self.status,
                "busy": self.busy,
                "active_device": self.active_key,
                "error": self.error,
                "devices": [
                    {"key": key, "identifier": s.device.identifier, "kind": s.device.kind}
                    for key, s in self.sessions.items()
                ],
                "shots": rows,
            }

    def select_device(self, key: str) -> None:
        with self.lock:
            if self.busy or key == self.active_key:
                return
            self.busy = True
            self.status = f"Switching to {key}..."
        threading.Thread(target=self._switch_worker, args=(key,), daemon=True).start()

    def _switch_worker(self, key: str) -> None:
        try:
            prev = self.sessions.get(self.active_key) if self.active_key else None
            if prev and prev.flutter_proc:
                self._set_status(f"Stopping app on {prev.key} (device stays open)...")
                flutter_app.stop(prev.flutter_proc)
                prev.flutter_proc = None
                prev.app_running = False

            self._activate(key)

            with self.lock:
                self.active_key = key
                self.status = f"{key}: app running — capture away"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
                self.status = "Error — see message below"
        finally:
            with self.lock:
                self.busy = False

    def restart_device(self, key: str) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.status = f"Restarting {key}..."
        threading.Thread(target=self._restart_worker, args=(key,), daemon=True).start()

    def _restart_worker(self, key: str) -> None:
        try:
            session = self.sessions[key]
            session.identifier = None
            session.flutter_proc = None
            session.app_running = False
            session.we_booted = False

            self._activate(key)

            with self.lock:
                self.active_key = key
                self.status = f"{key}: app running — capture away"
                self.error = None
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = str(exc)
                self.status = "Error — see message below"
        finally:
            with self.lock:
                self.busy = False

    def _activate(self, key: str) -> None:
        """Boots the device (if needed) and launches the app on it, updating that session in place."""
        session = self.sessions[key]
        device = session.device

        if device.kind == "ios":
            udid = ios.find_udid(device.identifier)
            was_running = ios.device_state(udid) == "Booted"
            self._set_status(
                f"{device.identifier} already running, reusing it."
                if was_running
                else f"Booting {device.identifier}..."
            )
            ios.boot(udid)
            session.identifier = udid
            session.we_booted = not was_running
        else:
            existing = android.find_running_serial(device.identifier)
            if existing:
                self._set_status(f"{device.identifier} already running as {existing}, reusing it.")
                session.identifier = existing
                session.we_booted = False
            else:
                self._set_status(f"Booting emulator {device.identifier}...")
                android.boot(device.identifier)
                session.identifier = android.wait_for_serial()
                session.we_booted = True

        existing_pid = flutter_app.find_running_pid(session.identifier)
        if existing_pid:
            self._set_status(f"{self.cfg.app.name} already running on {key} (pid {existing_pid}), reusing it.")
            session.flutter_proc = None
        else:
            self._set_status(f"Launching {self.cfg.app.name} on {key}... (this can take a minute)")
            proc, log_path = flutter_app.launch(self.cfg.app.flutter_dir, session.identifier)
            session.flutter_proc = proc
            flutter_app.wait_until_ready(log_path)
        session.app_running = True

    def capture(self, raw_shot_name: str) -> str:
        shot_id = _slugify(raw_shot_name)
        if not shot_id:
            raise ValueError("Shot name can't be empty")
        with self.lock:
            if not self.active_key:
                raise RuntimeError("No active device")
            session = self.sessions[self.active_key]
            if not session.app_running:
                raise RuntimeError("App not running yet")
        dest = self.cfg.raw_dir / session.key / f"{shot_id}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if session.device.kind == "ios":
            ios.screenshot(session.identifier, dest)
        else:
            android.screenshot(session.identifier, dest)
        self._set_status(f"Captured '{shot_id}' for {session.key}")

        lang = self.current_lang
        if shot_id not in titles_store.load_titles(self.cfg, lang):
            threading.Thread(target=self._suggest_title_worker, args=(lang, shot_id, dest), daemon=True).start()
        return shot_id

    def _suggest_title_worker(self, lang: str, shot_id: str, image_path: Path) -> None:
        try:
            self._set_status(f"Asking claude for a title for '{shot_id}'...")
            suggestion = ai_titles.suggest_title(image_path, self.cfg.app.name, lang=lang)
            titles_store.save_title(self.cfg, lang, shot_id, suggestion["title"], suggestion.get("subtitle", ""))
            self._set_status(f"Title for '{shot_id}': {suggestion['title']!r}")
        except ai_titles.TitleSuggestionError as exc:
            self._set_status(f"Title suggestion failed for '{shot_id}': {exc}")

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

    def delete_shot(self, shot_id: str) -> None:
        with self.lock:
            lang = self.current_lang
            for key in self.sessions:
                for base_dir in (self.cfg.raw_dir, self.cfg.store_dir(lang)):
                    path = base_dir / key / f"{shot_id}.png"
                    if path.exists():
                        path.unlink()
            titles_store.delete_title(self.cfg, lang, shot_id)
            self.status = f"Deleted '{shot_id}'"

    def shutdown(self) -> None:
        for session in self.sessions.values():
            if session.flutter_proc:
                flutter_app.stop(session.flutter_proc)
            if session.we_booted and session.identifier:
                try:
                    if session.device.kind == "ios":
                        ios.shutdown(session.identifier)
                    else:
                        android.kill(session.identifier)
                except Exception:  # noqa: BLE001
                    pass


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

    @app.post("/api/select-device")
    def select_device():
        studio.select_device(request.json["key"])
        return jsonify({"ok": True})

    @app.post("/api/restart-device")
    def restart_device_route():
        studio.restart_device(request.json["key"])
        return jsonify({"ok": True})

    @app.post("/api/select-language")
    def select_language():
        try:
            studio.set_language(request.json["lang"])
            return jsonify({"ok": True})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/capture")
    def capture():
        try:
            shot_id = studio.capture(request.json["shot_name"])
            return jsonify({"ok": True, "shot_id": shot_id})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 400

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

    @app.post("/api/quit")
    def quit_route():
        studio.shutdown()

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
    try:
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        app.studio.shutdown()  # type: ignore[attr-defined]
