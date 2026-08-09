from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

# Set on the first open_url() call in a capture session so we can warn about
# the iOS deep-link confirmation sheet before it appears.
_first_open_url_in_session = True


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
  if not (exists process "Simulator") then return "no"
  tell process "Simulator"
    try
      if exists (first button whose name is "Open") then return "yes"
    end try
    try
      if exists (button "Open" of sheet 1 of window 1) then return "yes"
    end try
    try
      if exists (button "Open" of window 1) then return "yes"
    end try
  end tell
end tell
return "no"
'''


_ACCESSIBILITY_PROBE_SCRIPT = '''
tell application "System Events"
  return name of first process whose frontmost is true
end tell
'''


def _system_events_available() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e", _ACCESSIBILITY_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


def _dialog_present() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e", _DIALOG_EXISTS_SCRIPT], capture_output=True, text=True, timeout=3
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip() == "yes"
    except Exception:
        return False


def _can_see_simulator_ui() -> bool:
    script = '''
tell application "System Events"
  if not (exists process "Simulator") then return "no"
  tell process "Simulator"
    try
      if (count of windows) > 0 then return "yes"
    end try
    try
      if exists (first button whose name is "Open") then return "yes"
    end try
  end tell
end tell
return "no"
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0 and result.stdout.strip() == "yes"
    except Exception:
        return False


def _wait_for_manual_dismiss(app_name: str, manual_timeout: float = 300.0, poll_interval: float = 0.3) -> None:
    print(
        f'\n  >>> Simulator may be showing "Open in {app_name}?" — tap **Open** in the '
        f"Simulator window now.\n"
        f"  >>> Capture resumes automatically once the dialog is gone "
        f"(or after you press Enter here if auto-detect is unavailable).\n"
    )
    sys.stdout.flush()

    if not _system_events_available() or not _can_see_simulator_ui():
        print(
            "  (Can't auto-detect the Simulator dialog — grant Accessibility to your "
            "terminal app under System Settings > Privacy & Security > Accessibility "
            "for automatic resume, or press Enter below after tapping Open.)"
        )
        try:
            input("  Press Enter after tapping Open in the Simulator (or if no dialog appeared)... ")
            print("  continuing.\n")
            return
        except EOFError:
            pass

    deadline = time.time() + manual_timeout
    while time.time() < deadline:
        if _system_events_available() and _can_see_simulator_ui() and not _dialog_present():
            print("  continuing.\n")
            return
        time.sleep(poll_interval)

    try:
        input("  Press Enter after tapping Open in the Simulator (or if no dialog appeared)... ")
        print("  continuing.\n")
        return
    except EOFError:
        pass

    raise TimeoutError(
        f"iOS 'Open in {app_name}?' dialog was never dismissed "
        f"(waited {manual_timeout:.0f}s) — tap Open in the Simulator window and re-run auto-capture."
    )


def _dismiss_open_dialog(app_name: str, settle: float = 2.0, manual_timeout: float = 300.0) -> None:
    """iOS shows an "Open in <App>?" confirmation sheet on *every*
    `simctl openurl` of a custom URL scheme (not just the first one per app
    session, despite what you might expect from it being a one-time system
    prompt in real usage) — auto-capture can't proceed past it since simctl
    has no API to answer it directly. Rather than script a UI click (fragile,
    depends on Accessibility permissions, and silently wrong if it ever
    clicks something else), this detects the dialog when possible and pauses:
    tap **Open** yourself in the Simulator window (also where you'd handle a
    real sign-in prompt, if a shot ever needs one) and capture continues on
    its own the moment it's gone.
    """
    time.sleep(settle)  # give the sheet a moment to actually appear

    can_auto_detect = _system_events_available() and _can_see_simulator_ui()
    if not can_auto_detect:
        _wait_for_manual_dismiss(app_name, manual_timeout=manual_timeout)
        return

    if not _dialog_present():
        return

    _wait_for_manual_dismiss(app_name, manual_timeout=manual_timeout)


def open_url(udid: str, url: str, app_name: str = "App") -> None:
    """Opens a deep link (or any URL) in the simulator, e.g. for auto-capture routes."""
    global _first_open_url_in_session
    if _first_open_url_in_session:
        _first_open_url_in_session = False
        print(
            f"\n  Note: iOS may show an \"Open in {app_name}?\" confirmation on each "
            f"deep link — auto-capture will pause until you tap Open.\n"
            f"  If it never pauses but screenshots show the dialog instead of your app, "
            f"grant Accessibility to your terminal under "
            f"System Settings > Privacy & Security > Accessibility.\n"
        )
        sys.stdout.flush()

    _simctl("openurl", udid, url)
    _dismiss_open_dialog(app_name)


def screenshot(udid: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _simctl("io", udid, "screenshot", str(dest))


def shutdown(udid: str) -> None:
    if device_state(udid) == "Booted":
        _simctl("shutdown", udid)
