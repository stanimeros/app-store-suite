from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

READY_MARKERS = ("Flutter run key commands", "A Dart VM Service")
FAILURE_MARKERS = (
    "Gradle build failed",
    "Xcode build failed",
    "Error launching application",
    "Could not build the application for the simulator",
)


class FlutterLaunchError(RuntimeError):
    pass


def launch(flutter_dir: Path, device_id: str) -> tuple[subprocess.Popen, Path]:
    """Starts `flutter run -d <device_id>` in the background, logging to a temp file.

    Debug mode, not release: the iOS Simulator only supports debug builds via
    `flutter run` (release/profile need physical hardware). Make sure the target
    app sets `debugShowCheckedModeBanner: false` so the DEBUG ribbon doesn't show
    up in screenshots.
    """
    log_path = Path(tempfile.gettempdir()) / f"shotstudio_flutter_{device_id.replace(':', '_').replace('/', '_')}.log"
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        ["flutter", "run", "-d", device_id],
        cwd=flutter_dir,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        text=True,
    )
    return proc, log_path


def wait_until_ready(log_path: Path, timeout: float = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists():
            text = log_path.read_text(errors="ignore")
            if any(marker in text for marker in READY_MARKERS):
                return
            for marker in FAILURE_MARKERS:
                if marker in text:
                    raise FlutterLaunchError(f"flutter run failed ({marker!r}); see {log_path}")
        time.sleep(2)
    raise TimeoutError(f"flutter run did not become ready within {timeout}s; see {log_path}")


def stop(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin:
            proc.stdin.write("q\n")
            proc.stdin.flush()
        proc.wait(timeout=15)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
