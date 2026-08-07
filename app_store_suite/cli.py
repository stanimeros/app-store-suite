from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ai_titles
from . import devices as devices_mod
from . import ship
from . import style_choices, titles_store
from .autocapture import run_auto_capture
from .capture import android
from .compose import compose_all
from .config import load_config
from .feature_graphic import generate_feature_graphic
from .frames import fetch as frames_fetch
from .icons import generate_play_store_icon
from .store_listing import fetch_current_listing, generate_store_listing
from .style_preview import generate_previews
from .style_variants import VARIANTS

DEFAULT_SYSTEM_IMAGE = "system-images;android-37.0;google_apis_playstore_ps16k;arm64-v8a"
DEFAULT_DEVICE_PROFILE = {"phone": "pixel_6", "tablet": "pixel_tablet"}


def cmd_setup(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)

    print("Checking Android SDK tools...")
    try:
        existing = set(android.list_avds())
        print(f"  found AVDs: {sorted(existing) or '(none)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: could not list AVDs ({exc})")
        existing = set()

    for key, device in cfg.devices.items():
        if device.kind == "android" and device.identifier not in existing:
            profile = DEFAULT_DEVICE_PROFILE[devices_mod.device_class(device)]
            print(f"  creating missing AVD '{device.identifier}' for device '{key}' (profile '{profile}')...")
            android.create_avd(device.identifier, DEFAULT_SYSTEM_IMAGE, profile)
            print(f"  created '{device.identifier}'")

    print("\nPrefetching device frames...")
    needed = set()
    for device in cfg.devices.values():
        spec = devices_mod.resolve_frame(device)
        if spec.frame_file:
            needed.add(spec.frame_file)
        else:
            print(f"  no frameit-frames match for '{device.identifier}'; will use procedural frame")
    fetched = frames_fetch.prefetch(needed)
    print(f"  cache dir: {frames_fetch.cache_dir()}")
    print(f"  newly fetched: {fetched or '(all cached already)'}")

    print("\nSetup complete.")


def cmd_auto_capture(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    run_auto_capture(cfg, only_device=args.device, render_delay=args.render_delay)


def cmd_ui(args: argparse.Namespace) -> None:
    from .webui import launch_web_ui  # deferred: flask only needed here

    cfg = load_config(args.config)
    launch_web_ui(cfg, port=args.port)


def cmd_compose(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    langs = [args.lang] if args.lang else cfg.languages
    for lang in langs:
        outputs = compose_all(cfg, lang, only_device=args.device)
        print(f"\n[{lang}] {len(outputs)} store screenshot(s) written under {cfg.store_dir(lang)}")


def cmd_style_preview(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    device_key = args.device or next(iter(cfg.devices))
    lang = args.lang or cfg.default_language
    shot_ids = args.shot.split(",") if args.shot else None

    sheets = generate_previews(cfg, device_key, lang, shot_ids)
    print(f"Variants: {', '.join(VARIANTS)}")
    for path in sheets:
        print(f"  wrote {path}")
    print("\nReview each *_compare.png, then run 'appstoresuite style-pick' to choose one per shot.")


def cmd_style_pick(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)

    if args.list:
        choices = style_choices.load_choices(cfg)
        print(f"Available variants: {', '.join(VARIANTS)}")
        print("Current per-shot choices:")
        if not choices:
            print("  (none — every shot uses the config's default style)")
        for shot_id, variant in sorted(choices.items()):
            print(f"  {shot_id}: {variant}")
        return

    if not args.shot:
        raise SystemExit("--shot is required (unless using --list)")

    if args.clear:
        style_choices.clear_choice(cfg, args.shot)
        print(f"Cleared style override for '{args.shot}' (will use the config's default style)")
        return

    if not args.variant:
        raise SystemExit("--variant is required (unless using --clear or --list)")

    style_choices.save_choice(cfg, args.shot, args.variant)
    print(f"'{args.shot}' will now use the '{args.variant}' style on next compose")


def cmd_store_icon(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    dest = generate_play_store_icon(cfg)
    print(f"Play Store icon (512x512) written to {dest}")


def cmd_feature_graphic(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    lang = args.lang or cfg.default_language
    headline = args.headline or f"{cfg.app.name}"
    dest = generate_feature_graphic(cfg, lang, headline, args.subtitle or "")
    print(f"Feature graphic written to {dest}")


def cmd_store_listing(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    lang = args.lang or cfg.default_language
    dest = generate_store_listing(cfg, lang)
    print(f"Store listing 'proposed' copy written to {dest}")


def cmd_fetch_listing(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    lang = args.lang or cfg.default_language
    dest = fetch_current_listing(cfg, lang)
    print(f"Store listing 'current' copy fetched into {dest}")


def cmd_translate_titles(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    source_titles = titles_store.load_titles(cfg, args.from_lang)
    if not source_titles:
        print(f"No titles found for '{args.from_lang}' at {cfg.titles_path(args.from_lang)}")
        return

    print(f"Translating {len(source_titles)} shot title(s) from {args.from_lang} -> {args.to_lang}...")
    translated = ai_titles.translate_titles(source_titles, cfg.app.name, args.from_lang, args.to_lang)

    dest = cfg.titles_path(args.to_lang)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(translated, indent=2, ensure_ascii=False))
    print(f"Wrote {len(translated)} translated title(s) to {dest}")


def _resolve_project_dir(args: argparse.Namespace) -> Path:
    """--project-dir wins if given; otherwise derive from --config's flutter_dir;
    otherwise fall back to the current directory (if it looks like a Flutter project)."""
    if args.project_dir:
        return Path(args.project_dir)
    if args.config:
        return load_config(args.config).app.flutter_dir
    cwd = Path.cwd()
    if (cwd / "pubspec.yaml").exists():
        return cwd
    raise SystemExit(
        "Couldn't determine the project directory: pass --project-dir, pass --config, "
        "or run this from a Flutter project root (containing pubspec.yaml)."
    )


def cmd_bump_version(args: argparse.Namespace) -> None:
    old, new = ship.bump_version(_resolve_project_dir(args))
    print(f"Bumped version: {old} -> {new}")


def cmd_ship_ios(args: argparse.Namespace) -> None:
    ship.ship_ios(_resolve_project_dir(args), lane=args.lane)
    print("Uploaded to TestFlight.")


def cmd_ship_android(args: argparse.Namespace) -> None:
    ship.ship_android(_resolve_project_dir(args), lane=args.lane)
    print("Uploaded to Play Store internal testing.")


def cmd_translate_arb(args: argparse.Namespace) -> None:
    ship.translate_arb(_resolve_project_dir(args), activate_source=args.activate_source)
    print("ARB strings translated and l10n classes regenerated.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="appstoresuite", description="Store-asset automation for Flutter apps")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Verify tooling, create missing Android AVDs, prefetch device frames")
    p_setup.add_argument("--config", required=True)
    p_setup.set_defaults(func=cmd_setup)

    p_auto = sub.add_parser(
        "auto-capture",
        help="Unattended capture: opens each configured shot's deep link and screenshots it, no navigation needed",
    )
    p_auto.add_argument("--config", required=True)
    p_auto.add_argument("--device", help="Only capture this device key from the config")
    p_auto.add_argument(
        "--render-delay", type=float, default=2.0, help="Seconds to wait after opening a deep link before screenshotting"
    )
    p_auto.set_defaults(func=cmd_auto_capture)

    p_ui = sub.add_parser("ui", help="Open the local web control panel: auto-capture, preview, metadata, ship")
    p_ui.add_argument("--config", required=True)
    p_ui.add_argument("--port", type=int, default=5175)
    p_ui.set_defaults(func=cmd_ui)

    p_compose = sub.add_parser("compose", help="Frame + brand raw screenshots into store-ready images")
    p_compose.add_argument("--config", required=True)
    p_compose.add_argument("--device", help="Only compose this device key from the config")
    p_compose.add_argument("--lang", help="Only compose this language (defaults to all configured languages)")
    p_compose.set_defaults(func=cmd_compose)

    p_style_preview = sub.add_parser(
        "style-preview",
        help="Render every style variant for one or more shots side by side, for comparison",
    )
    p_style_preview.add_argument("--config", required=True)
    p_style_preview.add_argument("--device", help="Device key to preview with (defaults to the first configured device)")
    p_style_preview.add_argument(
        "--lang", help="Language to preview with (style is language-independent; defaults to the first configured language)"
    )
    p_style_preview.add_argument("--shot", help="Comma-separated shot ids to preview (defaults to all shots)")
    p_style_preview.set_defaults(func=cmd_style_preview)

    p_style_pick = sub.add_parser(
        "style-pick",
        help="Choose a style variant for one shot id (overrides the config's default style for it on future composes)",
    )
    p_style_pick.add_argument("--config", required=True)
    p_style_pick.add_argument("--shot", help="Shot id to set/clear (required unless --list)")
    p_style_pick.add_argument("--variant", help="Style variant name to use for this shot")
    p_style_pick.add_argument("--clear", action="store_true", help="Remove this shot's override, reverting to the config's default style")
    p_style_pick.add_argument("--list", action="store_true", help="List available variants and current per-shot choices")
    p_style_pick.set_defaults(func=cmd_style_pick)

    p_icon = sub.add_parser("store-icon", help="Generate the 512x512 Play Store app icon")
    p_icon.add_argument("--config", required=True)
    p_icon.set_defaults(func=cmd_store_icon)

    p_fg = sub.add_parser("feature-graphic", help="Generate the 1024x500 Play Store feature graphic")
    p_fg.add_argument("--config", required=True)
    p_fg.add_argument("--lang", help="Language folder to write into (defaults to the first configured language)")
    p_fg.add_argument("--headline", help="Override the headline text (defaults to app name)")
    p_fg.add_argument("--subtitle", help="Optional subtitle text shown below the headline")
    p_fg.set_defaults(func=cmd_feature_graphic)

    p_listing = sub.add_parser(
        "store-listing", help="Draft 'proposed' Google Play / App Store listing copy via the claude CLI"
    )
    p_listing.add_argument("--config", required=True)
    p_listing.add_argument("--lang", help="Language folder to write into (defaults to the first configured language)")
    p_listing.set_defaults(func=cmd_store_listing)

    p_fetch_listing = sub.add_parser(
        "fetch-listing",
        help="Fetch the currently-live listing copy from App Store Connect / Play Console into 'current'",
    )
    p_fetch_listing.add_argument("--config", required=True)
    p_fetch_listing.add_argument("--lang", help="Language to fetch (defaults to the first configured language)")
    p_fetch_listing.set_defaults(func=cmd_fetch_listing)

    p_translate = sub.add_parser(
        "translate-titles", help="Translate one language's shot titles/subtitles into another via the claude CLI"
    )
    p_translate.add_argument("--config", required=True)
    p_translate.add_argument("--from", dest="from_lang", required=True, help="Source language code, e.g. en")
    p_translate.add_argument("--to", dest="to_lang", required=True, help="Target language code, e.g. el")
    p_translate.set_defaults(func=cmd_translate_titles)

    def _add_project_dir_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--project-dir",
            help="Flutter project root. Defaults to --config's flutter_dir, or the "
            "current directory if it contains pubspec.yaml.",
        )
        p.add_argument("--config", help="Derive --project-dir from this config's flutter_dir")

    p_bump = sub.add_parser("bump-version", help="Bump pubspec.yaml's PATCH and +BUILD together")
    _add_project_dir_args(p_bump)
    p_bump.set_defaults(func=cmd_bump_version)

    p_ship_ios = sub.add_parser(
        "ship-ios", help="Build the ipa and upload to TestFlight via `fastlane ios <lane>`"
    )
    _add_project_dir_args(p_ship_ios)
    p_ship_ios.add_argument("--lane", default="ship_testflight", help="Fastlane lane name (default: ship_testflight)")
    p_ship_ios.set_defaults(func=cmd_ship_ios)

    p_ship_android = sub.add_parser(
        "ship-android", help="Build the App Bundle and upload to Play Store internal testing via `fastlane android <lane>`"
    )
    _add_project_dir_args(p_ship_android)
    p_ship_android.add_argument("--lane", default="ship_internal", help="Fastlane lane name (default: ship_internal)")
    p_ship_android.set_defaults(func=cmd_ship_android)

    p_translate_arb = sub.add_parser(
        "translate-arb", help="Translate missing Flutter ARB strings (arb_translate) and regenerate l10n classes"
    )
    _add_project_dir_args(p_translate_arb)
    p_translate_arb.add_argument(
        "--activate-source",
        help="Override arb_translate source (absolute, or relative to --project-dir). "
        "Defaults to app-store-suite's own vendored copy if arb_translate isn't already on PATH.",
    )
    p_translate_arb.set_defaults(func=cmd_translate_arb)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
