from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


class AvdNotFound(RuntimeError):
    pass


def sdk_root() -> Path:
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            return Path(os.environ[var])
    default = Path.home() / "Library" / "Android" / "sdk"
    if default.exists():
        return default
    raise RuntimeError(
        "Android SDK not found. Set ANDROID_HOME or install it at ~/Library/Android/sdk"
    )


def _adb() -> str:
    return str(sdk_root() / "platform-tools" / "adb")


def _emulator() -> str:
    return str(sdk_root() / "emulator" / "emulator")


def _avdmanager() -> str:
    return str(sdk_root() / "cmdline-tools" / "latest" / "bin" / "avdmanager")


def _java_home() -> str | None:
    if os.environ.get("JAVA_HOME"):
        return os.environ["JAVA_HOME"]
    studio_jbr = Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home")
    if studio_jbr.exists():
        return str(studio_jbr)
    return None


def _env_with_java() -> dict:
    env = os.environ.copy()
    java_home = _java_home()
    if java_home:
        env["JAVA_HOME"] = java_home
    return env


def list_avds() -> list[str]:
    result = subprocess.run([_emulator(), "-list-avds"], capture_output=True, text=True, check=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def create_avd(name: str, package: str, device_profile: str) -> None:
    """package e.g. 'system-images;android-37.0;google_apis_playstore_ps16k;arm64-v8a'"""
    subprocess.run(
        [_avdmanager(), "create", "avd", "--name", name, "--package", package,
         "--device", device_profile, "--force"],
        input="no\n",  # decline "create custom hardware profile"
        capture_output=True, text=True, check=True, env=_env_with_java(),
    )


def connected_serials() -> list[str]:
    result = subprocess.run([_adb(), "devices"], capture_output=True, text=True, check=True)
    return [
        line.split()[0]
        for line in result.stdout.splitlines()[1:]
        if line.strip().endswith("device") and line.startswith("emulator-")
    ]


def find_running_serial(avd_name: str) -> str | None:
    """Returns the serial of an already-running emulator for this AVD, if any."""
    for serial in connected_serials():
        result = subprocess.run(
            [_adb(), "-s", serial, "emu", "avd", "name"], capture_output=True, text=True
        )
        name = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if name == avd_name:
            return serial
    return None


def boot(avd_name: str) -> subprocess.Popen | None:
    """Starts the AVD if it isn't already running. Returns None if it was already up."""
    if find_running_serial(avd_name):
        return None
    if avd_name not in list_avds():
        raise AvdNotFound(f"No AVD named '{avd_name}'. Run `appstoresuite setup` to create it.")
    proc = subprocess.Popen(
        [_emulator(), "-avd", avd_name, "-no-snapshot-load", "-no-boot-anim"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_serial(timeout: float = 180) -> str:
    """Waits for a single emulator device to show up in `adb devices` and finish booting."""
    deadline = time.time() + timeout
    serial = None
    while time.time() < deadline and serial is None:
        result = subprocess.run([_adb(), "devices"], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines()[1:]:
            if line.strip().endswith("device") and line.startswith("emulator-"):
                serial = line.split()[0]
                break
        if serial is None:
            time.sleep(1)
    if serial is None:
        raise TimeoutError("No emulator serial appeared in `adb devices`")

    while time.time() < deadline:
        result = subprocess.run(
            [_adb(), "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True,
        )
        if result.stdout.strip() == "1":
            return serial
        time.sleep(2)
    raise TimeoutError(f"Emulator {serial} did not finish booting within {timeout}s")


def open_url(serial: str, url: str) -> None:
    """Opens a deep link (or any URL) via an ACTION_VIEW intent, e.g. for auto-capture routes."""
    subprocess.run(
        [_adb(), "-s", serial, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url],
        capture_output=True, text=True, check=True,
    )


def screenshot(serial: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [_adb(), "-s", serial, "exec-out", "screencap", "-p"],
        capture_output=True, check=True,
    )
    dest.write_bytes(result.stdout)


def kill(serial: str) -> None:
    subprocess.run([_adb(), "-s", serial, "emu", "kill"], capture_output=True)
