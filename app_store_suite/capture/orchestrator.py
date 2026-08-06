from __future__ import annotations

from contextlib import contextmanager

from ..config import DeviceConfig, StudioConfig
from . import android, flutter_app, ios


@contextmanager
def device_session(cfg: StudioConfig, key: str, device: DeviceConfig):
    """Boots the device (if needed), launches the app, and yields (take_screenshot,
    open_url) — tearing both down afterward. Used by auto-capture, the only capture
    path app-store-suite has (see autocapture.py).
    """
    if device.kind == "ios":
        udid = ios.find_udid(device.identifier)
        was_running = ios.device_state(udid) == "Booted"
        print(
            f"Simulator {device.identifier} already running, reusing it."
            if was_running
            else f"Booting simulator {device.identifier} ({udid})..."
        )
        ios.boot(udid)
        identifier = udid
        take_screenshot = lambda dest: ios.screenshot(udid, dest)  # noqa: E731
        open_url = lambda url: ios.open_url(udid, url)  # noqa: E731
    else:
        existing_serial = android.find_running_serial(device.identifier)
        if existing_serial:
            print(f"Emulator {device.identifier} already running as {existing_serial}, reusing it.")
            identifier = existing_serial
            was_running = True
        else:
            print(f"Booting emulator {device.identifier}...")
            android.boot(device.identifier)
            identifier = android.wait_for_serial()
            print(f"Emulator ready as {identifier}")
            was_running = False
        take_screenshot = lambda dest: android.screenshot(identifier, dest)  # noqa: E731
        open_url = lambda url: android.open_url(identifier, url)  # noqa: E731

    flutter_proc = None
    try:
        print(f"Launching {cfg.app.name} (flutter run)... this can take a minute")
        flutter_proc, log_path = flutter_app.launch(cfg.app.flutter_dir, identifier)
        flutter_app.wait_until_ready(log_path)
        yield take_screenshot, open_url
    finally:
        if flutter_proc:
            flutter_app.stop(flutter_proc)
        if was_running:
            print(f"Leaving {device.identifier} running (it was already open).")
        else:
            print(f"Shutting down {device.identifier}...")
            if device.kind == "ios":
                ios.shutdown(identifier)
            else:
                android.kill(identifier)
