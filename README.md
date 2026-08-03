# screenshot-studio

Store-asset automation for Flutter apps: boots simulators/emulators one at a time,
guides you through capturing each feature screenshot, then composites the raw
captures into framed, titled, store-ready marketing images. Also generates the
512x512 Play Store app icon and the 1024x500 Play Store feature graphic.

Reusable across apps — everything is driven by a per-app YAML config under `configs/`.

## Install

```bash
cd screenshot-studio
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Usage

Point a config at your Flutter project (see `configs/chronal.yaml` for a full example:
app metadata, 2 iOS + 2 Android devices, the list of shots with titles, and background/
font style).

```bash
# One-time: create any missing Android AVDs, cache device bezel frames
shotstudio setup --config configs/chronal.yaml

# Boots each configured device in turn. For each one it tells you which screen to
# navigate to; press Enter to capture, 's' to skip. Shuts the device down before
# moving to the next one.
shotstudio capture --config configs/chronal.yaml
shotstudio capture --config configs/chronal.yaml --device ios_phone   # just one device

# Composites output/raw/<device>/<shot>.png into output/store/<device>/<shot>.png:
# device bezel frame (or a clean rounded-rect fallback), solid background color,
# and the shot's title rendered in Poppins.
shotstudio compose --config configs/chronal.yaml

# 512x512 Play Store app icon, resized from app.icon_source.
shotstudio store-icon --config configs/chronal.yaml

# 1024x500 Play Store feature graphic.
shotstudio feature-graphic --config configs/chronal.yaml --headline "Plan every trip"
```

## How device frames work

Bezel images and screen-offset metadata come from
[fastlane/frameit-frames](https://github.com/fastlane/frameit-frames) (MIT), fetched
on demand and cached at `~/.cache/screenshot-studio/frames`. The mapping from a
simulator/AVD name to a frame asset lives in `screenshot_studio/devices.py`
(`FRAME_MAP`) — frameit's device coverage skews toward real iOS/Android hardware
names, so `resolve_frame()` looks up by exact identifier; add an entry there for any
new device. If nothing matches (e.g. no modern Android tablet frame exists upstream),
`compose.py` falls back to a clean procedural rounded-corner + shadow frame instead
of failing.

## Adding a new app

Copy `configs/chronal.yaml`, point `app.flutter_dir` / `icon_source` at the new
project, and list its shots. If you add a device identifier not already in
`FRAME_MAP`, either add a frame mapping in `devices.py` or accept the procedural
fallback frame.
