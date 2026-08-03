from __future__ import annotations

from dataclasses import dataclass

from .config import DeviceConfig

# Maps a simulator/AVD identifier (as written in a config's `devices:` block) to the
# matching frame in fastlane/frameit-frames. `offset_key` indexes into that repo's
# latest/offsets.json (portrait), `frame_file` is the bezel PNG under latest/.
# Built by hand against the repo's directory listing (see plan) rather than guessed,
# since frameit's naming doesn't line up with simulator/AVD names.
FRAME_MAP: dict[str, dict[str, str]] = {
    "iPhone 17 Pro": {
        "offset_key": "iPhone 17 Pro",
        "frame_file": "Apple iPhone 17 Pro Silver.png",
    },
    "iPhone 17 Pro Max": {
        "offset_key": "iPhone 17 Pro Max",
        "frame_file": "Apple iPhone 17 Pro Max Silver.png",
    },
    "iPad Pro 13-inch (M5)": {
        "offset_key": "iPad Pro (12.9 inch) (4th generation)",
        "frame_file": "Apple iPad Pro (12.9-inch) (4th generation) Silver.png",
    },
    "iPad Air 13-inch (M4)": {
        "offset_key": "iPad Air (2019) 2",
        "frame_file": "Apple iPad Air (2019) 2 Silver.png",
    },
    # Uses the Galaxy S21 (centered punch-hole camera) rather than the Pixel 5 (its
    # frame's camera cutout sits off-center) to better match modern Android hardware.
    "Medium_Phone": {
        "offset_key": "Samsung Galaxy S21 5G",
        "frame_file": "Samsung Galaxy S21 5G Black.png",
    },
    "Pixel_8": {
        "offset_key": "Samsung Galaxy S21 5G",
        "frame_file": "Samsung Galaxy S21 5G Black.png",
    },
    # frameit-frames has no modern Android tablet bezel (no Pixel Tablet, nothing
    # current-gen) — this 2014 Nexus 9 is its only tablet entry. Used by explicit
    # choice over the procedural fallback despite the aspect ratio/bezel mismatch.
    "Pixel_Tablet": {
        "offset_key": "Nexus 9",
        "frame_file": "Nexus 9.png",
    },
}

# Final export canvas size per device class, matching current App Store Connect /
# Google Play Console accepted screenshot buckets.
#
# iOS: App Store Connect only accepts specific pixel buckets per device class —
# (1242x2688 or 1284x2778) for phone, (2064x2752 or 2048x2732) for tablet.
#
# Android: Play Console requires 16:9 or 9:16 aspect ratio, sides between 320-3840px
# (phone / 7" tablet) or 1080-7680px (10" tablet) — 1080x1920 and 1440x2560 are both
# exact 9:16 and fall inside the intersection of all three ranges, so the same pair
# of assets is valid for phone, 7" tablet, and 10" tablet listings alike.
_STORE_RESOLUTIONS = {
    ("ios", "phone"): (1284, 2778),
    ("ios", "tablet"): (2064, 2752),
    ("android", "phone"): (1080, 1920),
    ("android", "tablet"): (1440, 2560),
}

_TABLET_HINTS = ("ipad", "tablet", "pixel tablet", "nexus 9")


@dataclass
class FrameSpec:
    offset_key: str | None
    frame_file: str | None


def device_class(device: DeviceConfig) -> str:
    """Returns 'phone' or 'tablet' from the identifier text."""
    lowered = device.identifier.lower()
    return "tablet" if any(hint in lowered for hint in _TABLET_HINTS) else "phone"


def store_resolution(device: DeviceConfig) -> tuple[int, int]:
    return _STORE_RESOLUTIONS[(device.kind, device_class(device))]


def resolve_frame(device: DeviceConfig) -> FrameSpec:
    match = FRAME_MAP.get(device.identifier)
    if match is None:
        return FrameSpec(offset_key=None, frame_file=None)
    return FrameSpec(offset_key=match["offset_key"], frame_file=match["frame_file"])
