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

# Boots each configured device in turn (reusing it if already running/open),
# launches the app itself via `flutter run`, then tells you which screen to
# navigate to; press Enter to capture, 's' to skip. Shuts the device down
# afterward, unless it was already open before capture started.
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

# Google Play / App Store listing copy (app name, descriptions, keywords), generated
# from the shots' titles/subtitles via the `claude` CLI. Character counts against each
# store's limits are computed in Python, not trusted from the model's own output.
shotstudio store-listing --config configs/chronal.yaml
```

## Style options

`style:` in a config controls one consistent look for the whole app (not per-shot).
All fields are optional; omitting them keeps the original solid-background, upright,
undecorated look.

```yaml
style:
  background_color: "#FAFAF8"
  background_mode: "solid" # solid | auto | gradient
  gradient_color2: "#D8E6FF" # 2nd color for background_mode: gradient (top -> bottom)
  title_color: "#1A1A1A"
  font_bold: "Poppins-Bold.ttf"
  font_regular: "Poppins-Regular.ttf"
  layout: "centered" # centered | tilted
  tilt_degrees: 6 # rotation for layout: tilted; alternates left/right per shot
  decoration: "none" # none | shapes | svg
  decoration_color: "#1A1A1A" # for decoration: shapes (defaults to title_color)
  decoration_svg_dir: decorations # for decoration: svg — dir of .svg files, one picked
  # per shot deterministically, rasterized via CairoSVG and drawn behind the device at
  # low opacity. Path is relative to the config file's own directory.
```

`background_mode: auto` and `background_mode: gradient` are mutually exclusive —
`gradient` always uses `background_color`/`gradient_color2` and ignores per-shot
sampling. Tilt direction and svg/shape choice are both derived from a hash of the shot
id, so the same shot always renders the same way across devices and re-runs.

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

## Note on run mode

`capture` always launches the app with plain `flutter run` (debug mode) — the iOS
Simulator can't run release/profile builds, only physical devices can. Make sure
your app sets `debugShowCheckedModeBanner: false` on `MaterialApp` (already done
for chronal) so the red DEBUG ribbon doesn't show up in screenshots.

## Adding a new app

Copy `configs/chronal.yaml`, point `app.flutter_dir` / `icon_source` at the new
project, and list its shots. If you add a device identifier not already in
`FRAME_MAP`, either add a frame mapping in `devices.py` or accept the procedural
fallback frame.
