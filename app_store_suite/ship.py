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


# Action calls the `init`-generated Fastfile ships commented out. If a lane still
# contains these verbatim, `fastlane` will run it and exit 0 without having built or
# uploaded anything — so callers can't tell a real ship from a no-op stub by the
# fastlane exit code alone.
_STUB_MARKERS = {
    "ios": ('build_app(scheme: "Runner")', "upload_to_testflight"),
    "android": ('gradle(task: "bundle", build_type: "Release")', 'upload_to_play_store(track: "internal")'),
}


def _lane_is_stub(project_dir: Path, platform: str) -> bool:
    fastfile = project_dir / "fastlane" / "Fastfile"
    if not fastfile.exists():
        return False
    text = fastfile.read_text()
    return any(f"# {marker}" in text for marker in _STUB_MARKERS.get(platform, ()))


# Placeholder values `init`-generated Fastfiles ship in place of real credentials.
# If these are still present, fastlane will run for real and fail deep inside a
# Play/App Store Connect API call instead of at a clean, obvious checkpoint.
_PLACEHOLDER_CREDENTIAL_MARKERS = (
    "your-service-account.json",
    "TODO_ASC_KEY_ID",
    "TODO_ASC_ISSUER_ID",
)


def _missing_credentials(project_dir: Path) -> list[str]:
    fastfile = project_dir / "fastlane" / "Fastfile"
    if not fastfile.exists():
        return []
    text = fastfile.read_text()
    return [marker for marker in _PLACEHOLDER_CREDENTIAL_MARKERS if marker in text]


def _fastlane(project_dir: Path, platform: str, lane: str) -> None:
    _require("bundle", "gem install bundler")
    if not (project_dir / "Gemfile").exists():
        raise ShipError(
            f"no Gemfile at {project_dir} — run `appstoresuite init` to scaffold one, "
            "or add one yourself with `gem \"fastlane\"` in it"
        )
    missing = _missing_credentials(project_dir)
    if missing:
        raise ShipError(
            "fastlane/Fastfile still has placeholder credentials ("
            + ", ".join(missing)
            + ") — fill in ANDROID_PACKAGE_NAME/IOS_BUNDLE_ID/PLAY_JSON_KEY/ASC_KEY_ID/"
            "ASC_ISSUER_ID/ASC_KEY_PATH at the top of fastlane/Fastfile before shipping"
        )
    check = subprocess.run(["bundle", "check"], cwd=project_dir, capture_output=True)
    if check.returncode != 0:
        _run(["bundle", "install"], cwd=project_dir)
    _run(["bundle", "exec", "fastlane", platform, lane], cwd=project_dir)


def ship_ios(project_dir: Path, lane: str = "ship_testflight") -> bool:
    """Builds the ipa and uploads it to TestFlight via `fastlane ios <lane>`.

    Returns False if the lane still looks like the unfilled `init` stub (fastlane
    ran and exited 0, but the build/upload steps are commented out and nothing
    was actually shipped).
    """
    _require("flutter")
    _fastlane(project_dir, "ios", lane)
    return not _lane_is_stub(project_dir, "ios")


def ship_android(project_dir: Path, lane: str = "ship_internal") -> bool:
    """Builds the App Bundle and uploads it to internal testing via `fastlane android <lane>`.

    Returns False if the lane still looks like the unfilled `init` stub (fastlane
    ran and exited 0, but the build/upload steps are commented out and nothing
    was actually shipped).
    """
    _require("flutter")
    _fastlane(project_dir, "android", lane)
    return not _lane_is_stub(project_dir, "android")


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
