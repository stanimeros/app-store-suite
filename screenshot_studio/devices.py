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
    # Only phone-class real hardware AVD frames exist in frameit-frames; there is no
    # modern Pixel Tablet frame, so android tablets fall back to the procedural frame
    # in compose.py unless a match is added here.
    "Medium_Phone": {
        "offset_key": "Google Pixel 5",
        "frame_file": "Google Pixel 5 Just Black.png",
    },
    "Pixel_8": {
        "offset_key": "Google Pixel 5",
        "frame_file": "Google Pixel 5 Just Black.png",
    },
}

# Final export canvas size per device class, matching current App Store Connect /
# Google Play Console accepted screenshot buckets.
_STORE_RESOLUTIONS = {
    ("ios", "phone"): (1290, 2796),  # iPhone 6.5"/6.7" bucket
    ("ios", "tablet"): (2064, 2752),  # iPad 12.9"/13" bucket
    ("android", "phone"): (1080, 1920),
    ("android", "tablet"): (1600, 2560),
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
