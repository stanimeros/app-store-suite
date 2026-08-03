from __future__ import annotations

from ..config import DeviceConfig, StudioConfig
from . import android, flutter_app, ios


def run_capture(cfg: StudioConfig, only_device: str | None = None) -> None:
    devices = (
        {only_device: cfg.devices[only_device]} if only_device else cfg.devices
    )
    for key, device in devices.items():
        print(f"\n=== {key} ({device.identifier}) ===")
        if device.kind == "ios":
            _run_ios_device(cfg, key, device)
        else:
            _run_android_device(cfg, key, device)


def _run_shot_loop(cfg: StudioConfig, device_key: str, take_screenshot) -> None:
    print("App is running. Walk through the shots below.")
    for shot in cfg.shots:
        prompt = f"[{device_key}] Navigate to: {shot.title!r} (id={shot.id}) — press Enter to capture, or 's' to skip: "
        answer = input(prompt).strip().lower()
        if answer == "s":
            print(f"  skipped {shot.id}")
            continue
        dest = cfg.raw_dir / device_key / f"{shot.id}.png"
        take_screenshot(dest)
        print(f"  saved {dest}")


def _run_ios_device(cfg: StudioConfig, key: str, device: DeviceConfig) -> None:
    udid = ios.find_udid(device.identifier)
    was_running = ios.device_state(udid) == "Booted"
    if was_running:
        print(f"Simulator {device.identifier} already running, reusing it.")
    else:
        print(f"Booting simulator {device.identifier} ({udid})...")
    ios.boot(udid)  # no-op boot if already up; still focuses the Simulator window

    flutter_proc = None
    try:
        print(f"Launching {cfg.app.name} (flutter run)... this can take a minute")
        flutter_proc, log_path = flutter_app.launch(cfg.app.flutter_dir, udid)
        flutter_app.wait_until_ready(log_path)
        _run_shot_loop(cfg, key, lambda dest: ios.screenshot(udid, dest))
    finally:
        if flutter_proc:
            flutter_app.stop(flutter_proc)
        if was_running:
            print(f"Leaving {device.identifier} running (it was already open).")
        else:
            print(f"Shutting down {device.identifier}...")
            ios.shutdown(udid)


def _run_android_device(cfg: StudioConfig, key: str, device: DeviceConfig) -> None:
    existing_serial = android.find_running_serial(device.identifier)
    if existing_serial:
        print(f"Emulator {device.identifier} already running as {existing_serial}, reusing it.")
        serial = existing_serial
        we_booted = False
    else:
        print(f"Booting emulator {device.identifier}...")
        android.boot(device.identifier)
        serial = android.wait_for_serial()
        print(f"Emulator ready as {serial}")
        we_booted = True

    flutter_proc = None
    try:
        print(f"Launching {cfg.app.name} (flutter run)... this can take a minute")
        flutter_proc, log_path = flutter_app.launch(cfg.app.flutter_dir, serial)
        flutter_app.wait_until_ready(log_path)
        _run_shot_loop(cfg, key, lambda dest: android.screenshot(serial, dest))
    finally:
        if flutter_proc:
            flutter_app.stop(flutter_proc)
        if we_booted:
            print(f"Killing emulator {device.identifier}...")
            android.kill(serial)
        else:
            print(f"Leaving emulator {device.identifier} running (it was already open).")
