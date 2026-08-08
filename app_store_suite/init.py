from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _detect_app_name(project_dir: Path) -> str | None:
    """Flutter package name from pubspec.yaml, e.g. `my_app` -> `My App`."""
    pubspec = project_dir / "pubspec.yaml"
    if not pubspec.exists():
        return None
    match = re.search(r"^name:\s*(\S+)", pubspec.read_text(), re.MULTILINE)
    if not match:
        return None
    return match.group(1).replace("_", " ").replace("-", " ").title()


def _detect_bundle_id(project_dir: Path) -> str | None:
    """iOS bundle id from the Runner target's PRODUCT_BUNDLE_IDENTIFIER, skipping
    the RunnerTests target."""
    pbxproj = project_dir / "ios" / "Runner.xcodeproj" / "project.pbxproj"
    if not pbxproj.exists():
        return None
    for match in re.finditer(r"PRODUCT_BUNDLE_IDENTIFIER\s*=\s*([\w.]+);", pbxproj.read_text()):
        value = match.group(1)
        if not value.endswith(".RunnerTests"):
            return value
    return None


def _detect_android_package(project_dir: Path) -> str | None:
    """Android applicationId from android/app/build.gradle(.kts)."""
    for name in ("build.gradle.kts", "build.gradle"):
        gradle = project_dir / "android" / "app" / name
        if not gradle.exists():
            continue
        match = re.search(r'applicationId\s*[= ]\s*"([\w.]+)"', gradle.read_text())
        if match:
            return match.group(1)
    return None


@dataclass
class ScaffoldResult:
    created: list[Path]
    skipped: list[Path]


def scaffold_project(
    project_dir: Path,
    config_path: Path | None = None,
    app_name: str | None = None,
    force: bool = False,
) -> ScaffoldResult:
    """Lays down starter files for a new app — app_store_suite.yaml, l10n.yaml
    (+ a template ARB file), .env.example, a fastlane skeleton, and a
    screenshot-router template for `auto-capture` — by copying them from
    templates/ (next to this package) and substituting `__PLACEHOLDER__`
    tokens where a value was auto-detected from the project.

    Never overwrites an existing file unless force=True.
    """
    created: list[Path] = []
    skipped: list[Path] = []

    def _write(path: Path, content: str) -> None:
        if path.exists() and not force:
            skipped.append(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        created.append(path)

    def _copy(template_relpath: str, dest: Path, substitutions: dict[str, str] | None = None) -> None:
        content = (TEMPLATES_DIR / template_relpath).read_text()
        for token, value in (substitutions or {}).items():
            content = content.replace(token, value)
        _write(dest, content)

    app_name = app_name or _detect_app_name(project_dir)
    bundle_id = _detect_bundle_id(project_dir)
    android_package = _detect_android_package(project_dir)

    config_path = config_path or (project_dir / "app_store_suite.yaml")
    example = (TEMPLATES_DIR / "app_store_suite.example.yaml").read_text()
    if app_name:
        example = example.replace("name: YourApp", f"name: {app_name}")
    if bundle_id:
        example = example.replace(
            "# bundle_id: com.yourcompany.yourapp", f"bundle_id: {bundle_id}"
        )
    if android_package:
        example = example.replace(
            "# android_package_name: com.yourcompany.yourapp", f"android_package_name: {android_package}"
        )
    _write(config_path, example)

    _copy("l10n.yaml", project_dir / "l10n.yaml")
    _copy(
        "app_en.arb",
        project_dir / "lib" / "l10n" / "app_en.arb",
        {"__APP_NAME__": app_name or "YourApp"},
    )
    _copy("env.example", project_dir / ".env.example")
    _copy(
        "fastlane/Fastfile",
        project_dir / "fastlane" / "Fastfile",
        {
            "__ANDROID_PACKAGE__": android_package or "com.yourcompany.yourapp",
            "__BUNDLE_ID__": bundle_id or "com.yourcompany.yourapp",
        },
    )
    _copy(
        "fastlane/Appfile",
        project_dir / "fastlane" / "Appfile",
        {
            "__APP_IDENTIFIER_LINE__": (
                f'app_identifier("{bundle_id}")' if bundle_id else '# app_identifier("com.yourcompany.yourapp")'
            ),
            "__PACKAGE_NAME_LINE__": (
                f'package_name("{android_package}")' if android_package else '# package_name("com.yourcompany.yourapp")'
            ),
        },
    )
    _copy("Gemfile", project_dir / "Gemfile")
    _copy("lib/debug/screenshot_router.dart", project_dir / "lib" / "debug" / "screenshot_router.dart")

    return ScaffoldResult(created=created, skipped=skipped)
