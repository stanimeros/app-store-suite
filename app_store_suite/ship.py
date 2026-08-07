from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class ShipError(RuntimeError):
    pass


def _require(cmd: str, hint: str = "") -> None:
    if shutil.which(cmd) is None:
        raise ShipError(f"'{cmd}' not found on PATH" + (f" ({hint})" if hint else ""))


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        raise ShipError(f"command failed ({result.returncode}): {' '.join(cmd)}")


_VERSION_RE = re.compile(r"^version:\s*(\d+)\.(\d+)\.(\d+)\+(\d+)\s*$")


def bump_version(project_dir: Path) -> tuple[str, str]:
    """Bumps pubspec.yaml's PATCH and +BUILD together, e.g. 1.0.6+9 -> 1.0.7+10."""
    pubspec = project_dir / "pubspec.yaml"
    if not pubspec.exists():
        raise ShipError(f"no pubspec.yaml at {pubspec}")

    lines = pubspec.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _VERSION_RE.match(line.rstrip("\n"))
        if m:
            major, minor, patch, build = (int(x) for x in m.groups())
            old = f"{major}.{minor}.{patch}+{build}"
            new = f"{major}.{minor}.{patch + 1}+{build + 1}"
            lines[i] = f"version: {new}\n"
            pubspec.write_text("".join(lines))
            return old, new

    raise ShipError(f"could not find a 'version: X.Y.Z+B' line in {pubspec}")


def _fastlane(project_dir: Path, platform: str, lane: str) -> None:
    _require("bundle", "gem install bundler")
    check = subprocess.run(["bundle", "check"], cwd=project_dir, capture_output=True)
    if check.returncode != 0:
        _run(["bundle", "install"], cwd=project_dir)
    _run(["bundle", "exec", "fastlane", platform, lane], cwd=project_dir)


def ship_ios(project_dir: Path, lane: str = "ship_testflight") -> None:
    """Builds the ipa and uploads it to TestFlight via `fastlane ios <lane>`."""
    _require("flutter")
    _fastlane(project_dir, "ios", lane)


def ship_android(project_dir: Path, lane: str = "ship_internal") -> None:
    """Builds the App Bundle and uploads it to internal testing via `fastlane android <lane>`."""
    _require("flutter")
    _fastlane(project_dir, "android", lane)


VENDORED_ARB_TRANSLATE = Path(__file__).parent.parent / "vendor" / "arb_translate"


def translate_arb(project_dir: Path, activate_source: str | Path | None = None) -> None:
    """Translates missing ARB strings with arb_translate, then regenerates l10n classes.

    Requires ARB_TRANSLATE_API_KEY in the environment or a .env file in project_dir.
    arb_translate itself is vendored with app-store-suite (vendor/arb_translate) and
    activated automatically the first time it's needed — pass activate_source to use
    a different fork instead (an absolute path, or one relative to project_dir).
    """
    import os

    env = os.environ.copy()
    env["PATH"] = f"{env.get('PATH', '')}:{Path.home() / '.pub-cache' / 'bin'}"

    dotenv = project_dir / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            env.setdefault(key, value.strip().strip('"').strip("'"))

    if not env.get("ARB_TRANSLATE_API_KEY"):
        raise ShipError("ARB_TRANSLATE_API_KEY is not set (add it to .env or export it)")

    if shutil.which("arb_translate") is None:
        source = Path(activate_source) if activate_source else VENDORED_ARB_TRANSLATE
        if not source.is_absolute():
            source = project_dir / source
        if not source.exists():
            raise ShipError(f"arb_translate source not found at {source}")
        _run(
            ["dart", "pub", "global", "activate", "--source", "path", str(source)],
            cwd=project_dir, env=env,
        )

    _run(["arb_translate"], cwd=project_dir, env=env)
    _run(["flutter", "pub", "get"], cwd=project_dir, env=env)
    _run(["flutter", "gen-l10n"], cwd=project_dir, env=env)
