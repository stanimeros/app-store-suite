from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


class SimulatorNotFound(RuntimeError):
    pass


def _simctl(*args: str) -> str:
    result = subprocess.run(
        ["xcrun", "simctl", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def find_udid(simulator_name: str) -> str:
    data = json.loads(_simctl("list", "devices", "available", "-j"))
    for runtime_devices in data["devices"].values():
        for dev in runtime_devices:
            if dev["name"] == simulator_name:
                return dev["udid"]
    raise SimulatorNotFound(
        f"No available iOS simulator named '{simulator_name}'. "
        f"Run `xcrun simctl list devices available` to see valid names."
    )


def device_state(udid: str) -> str:
    data = json.loads(_simctl("list", "devices", "-j"))
    for runtime_devices in data["devices"].values():
        for dev in runtime_devices:
            if dev["udid"] == udid:
                return dev["state"]
    return "Unknown"


def boot(udid: str, timeout: float = 120) -> None:
    if device_state(udid) != "Booted":
        _simctl("boot", udid)
    subprocess.run(["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", udid], check=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if device_state(udid) == "Booted":
            return
        time.sleep(1)
    raise TimeoutError(f"Simulator {udid} did not report Booted within {timeout}s")


def open_url(udid: str, url: str) -> None:
    """Opens a deep link (or any URL) in the simulator, e.g. for auto-capture routes."""
    _simctl("openurl", udid, url)


def screenshot(udid: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _simctl("io", udid, "screenshot", str(dest))


def shutdown(udid: str) -> None:
    if device_state(udid) == "Booted":
        _simctl("shutdown", udid)
