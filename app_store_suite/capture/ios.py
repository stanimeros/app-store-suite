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


_DIALOG_EXISTS_SCRIPT = '''
tell application "System Events"
  if exists process "Simulator" then
    tell process "Simulator"
      return exists (button "Open" of window 1)
    end tell
  end if
end tell
return false
'''


def _dialog_present() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e", _DIALOG_EXISTS_SCRIPT], capture_output=True, text=True, timeout=2
        )
        return result.stdout.strip() == "true"
    except Exception:
        # Most likely System Events lacks Accessibility access, so we can't
        # even detect the dialog — nothing to do but proceed and let it show
        # up in the screenshot if it's there.
        return False


def _dismiss_open_dialog(settle: float = 2.0, manual_timeout: float = 300.0, poll_interval: float = 0.3) -> None:
    """iOS shows an "Open in <App>?" confirmation sheet on *every*
    `simctl openurl` of a custom URL scheme (not just the first one per app
    session, despite what you might expect from it being a one-time system
    prompt in real usage) — auto-capture can't proceed past it since simctl
    has no API to answer it directly. Rather than script a UI click (fragile,
    depends on Accessibility permissions, and silently wrong if it ever
    clicks something else), this just detects the dialog and pauses: tap
    **Open** yourself in the Simulator window (also where you'd handle a
    real sign-in prompt, if a shot ever needs one) and capture continues on
    its own the moment it's gone.
    """
    time.sleep(settle)  # give the sheet a moment to actually appear
    if not _dialog_present():
        return

    print(
        '\n  Simulator is showing "Open in Chronal?" — tap Open in the Simulator '
        "window to continue (capture resumes automatically)..."
    )
    deadline = time.time() + manual_timeout
    while time.time() < deadline:
        if not _dialog_present():
            print("  continuing.\n")
            return
        time.sleep(poll_interval)
    raise TimeoutError(
        "iOS 'Open in Chronal?' dialog was never dismissed "
        f"(waited {manual_timeout:.0f}s) — tap Open in the Simulator window and re-run auto-capture."
    )


def open_url(udid: str, url: str) -> None:
    """Opens a deep link (or any URL) in the simulator, e.g. for auto-capture routes."""
    _simctl("openurl", udid, url)
    _dismiss_open_dialog()


def screenshot(udid: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _simctl("io", udid, "screenshot", str(dest))


def shutdown(udid: str) -> None:
    if device_state(udid) == "Booted":
        _simctl("shutdown", udid)
