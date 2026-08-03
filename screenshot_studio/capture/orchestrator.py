from __future__ import annotations

from ..config import DeviceConfig, StudioConfig
from . import android, ios


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


def _capture_shots(cfg: StudioConfig, device_key: str, take_screenshot) -> None:
    print(
        f"Launch {cfg.app.name} on this device now (flutter run, or from Xcode/Android Studio), "
        f"then walk through the shots below."
    )
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
    print(f"Booting simulator {device.identifier} ({udid})...")
    ios.boot(udid)
    try:
        _capture_shots(cfg, key, lambda dest: ios.screenshot(udid, dest))
    finally:
        print(f"Shutting down {device.identifier}...")
        ios.shutdown(udid)


def _run_android_device(cfg: StudioConfig, key: str, device: DeviceConfig) -> None:
    print(f"Booting emulator {device.identifier}...")
    proc = android.boot(device.identifier)
    try:
        serial = android.wait_for_serial()
        print(f"Emulator ready as {serial}")
        _capture_shots(cfg, key, lambda dest: android.screenshot(serial, dest))
    finally:
        print(f"Killing emulator {device.identifier}...")
        try:
            android.kill(serial)
        except NameError:
            proc.terminate()
