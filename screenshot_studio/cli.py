from __future__ import annotations

import argparse
import sys

from . import devices as devices_mod
from .capture import android
from .capture.orchestrator import run_capture
from .compose import compose_all
from .config import load_config
from .feature_graphic import generate_feature_graphic
from .frames import fetch as frames_fetch
from .icons import generate_play_store_icon

DEFAULT_TABLET_SYSTEM_IMAGE = "system-images;android-37.0;google_apis_playstore_ps16k;arm64-v8a"
DEFAULT_TABLET_DEVICE_PROFILE = "pixel_tablet"


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
            print(f"  creating missing AVD '{device.identifier}' for device '{key}'...")
            android.create_avd(device.identifier, DEFAULT_TABLET_SYSTEM_IMAGE, DEFAULT_TABLET_DEVICE_PROFILE)
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


def cmd_capture(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    run_capture(cfg, only_device=args.device)


def cmd_compose(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    outputs = compose_all(cfg, only_device=args.device)
    print(f"\n{len(outputs)} store screenshot(s) written under {cfg.store_dir}")


def cmd_store_icon(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    dest = generate_play_store_icon(cfg)
    print(f"Play Store icon (512x512) written to {dest}")


def cmd_feature_graphic(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    headline = args.headline or f"{cfg.app.name}"
    dest = generate_feature_graphic(cfg, headline)
    print(f"Feature graphic written to {dest}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="shotstudio", description="Store-asset automation for Flutter apps")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Verify tooling, create missing Android AVDs, prefetch device frames")
    p_setup.add_argument("--config", required=True)
    p_setup.set_defaults(func=cmd_setup)

    p_capture = sub.add_parser("capture", help="Boot each device and walk through capturing shots")
    p_capture.add_argument("--config", required=True)
    p_capture.add_argument("--device", help="Only capture this device key from the config")
    p_capture.set_defaults(func=cmd_capture)

    p_compose = sub.add_parser("compose", help="Frame + brand raw screenshots into store-ready images")
    p_compose.add_argument("--config", required=True)
    p_compose.add_argument("--device", help="Only compose this device key from the config")
    p_compose.set_defaults(func=cmd_compose)

    p_icon = sub.add_parser("store-icon", help="Generate the 512x512 Play Store app icon")
    p_icon.add_argument("--config", required=True)
    p_icon.set_defaults(func=cmd_store_icon)

    p_fg = sub.add_parser("feature-graphic", help="Generate the 1024x500 Play Store feature graphic")
    p_fg.add_argument("--config", required=True)
    p_fg.add_argument("--headline", help="Override the headline text (defaults to app name)")
    p_fg.set_defaults(func=cmd_feature_graphic)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
