# app-store-suite

Store-asset and shipping automation for Flutter apps: boots simulators/emulators one
at a time, guides you through capturing each feature screenshot, then composites the
raw captures into framed, titled, store-ready marketing images. Also generates the
512x512 Play Store app icon, the 1024x500 Play Store feature graphic, and Google
Play / App Store listing copy via the `claude` CLI — and ships builds to TestFlight /
Play Store internal testing via fastlane, and a Firebase backend via the firebase CLI.

Reusable across apps — everything is driven by a per-app YAML config under `configs/`,
and it installs as a single global `appstoresuite` command so it can be run from any
project directory, not just this one.

## Install

Installed once, globally, via [pipx](https://pipx.pypa.io/):

```bash
cd app-store-suite
pipx install --editable .
```

`--editable` means pulling future changes in this repo (`git pull`) takes effect
immediately, without reinstalling. Upgrading after a `git pull`:

```bash
pipx upgrade app-store-suite
```

## Usage

Run `appstoresuite` from anywhere, pointing `--config` at a YAML config that lives
in (or alongside) whichever app you're generating assets for. See
`configs/chronal.yaml` in this repo for a full example: app metadata, 2 iOS + 2
Android devices, the list of shots with titles, and background/font style. Copy that
file into your own project (or keep configs here under `configs/<app>.yaml`) and
point `app.flutter_dir` at that project's checkout.

```bash
# One-time: create any missing Android AVDs, cache device bezel frames
appstoresuite setup --config /path/to/your-app/configs/app.yaml

# Boots each configured device in turn (reusing it if already running/open),
# launches the app itself via `flutter run`, then tells you which screen to
# navigate to; press Enter to capture, 's' to skip. Shuts the device down
# afterward, unless it was already open before capture started.
appstoresuite capture --config /path/to/your-app/configs/app.yaml
appstoresuite capture --config /path/to/your-app/configs/app.yaml --device ios_phone   # just one device

# Composites output/raw/<device>/<shot>.png into output/store/<device>/<shot>.png:
# device bezel frame (or a clean rounded-rect fallback), solid background color,
# and the shot's title rendered in Poppins.
appstoresuite compose --config /path/to/your-app/configs/app.yaml

# 512x512 Play Store app icon, resized from app.icon_source.
appstoresuite store-icon --config /path/to/your-app/configs/app.yaml

# 1024x500 Play Store feature graphic.
appstoresuite feature-graphic --config /path/to/your-app/configs/app.yaml --headline "Plan every trip"

# Google Play / App Store listing copy (app name, descriptions, keywords), generated
# from the shots' titles/subtitles via the `claude` CLI. Character counts against each
# store's limits are computed in Python, not trusted from the model's own output.
appstoresuite store-listing --config /path/to/your-app/configs/app.yaml
```

## Shipping

These commands operate directly on a Flutter project checkout (`--project-dir`), no
YAML config needed — no per-shot/screenshot state, just build + upload plumbing.

```bash
# Bumps pubspec.yaml's PATCH and +BUILD together, e.g. 1.0.6+9 -> 1.0.7+10.
# Run once per release, before ship-ios / ship-android, so both platforms ship the
# same version.
appstoresuite bump-version --project-dir /path/to/your-app

# Builds the ipa and uploads it to TestFlight, via `bundle exec fastlane ios <lane>`.
# Requires a Fastfile with that lane (default: ship_testflight) already set up.
appstoresuite ship-ios --project-dir /path/to/your-app
appstoresuite ship-ios --project-dir /path/to/your-app --lane ship_testflight

# Builds the App Bundle and uploads it to Play Store internal testing, via
# `bundle exec fastlane android <lane>` (default lane: ship_internal).
appstoresuite ship-android --project-dir /path/to/your-app

# Translates missing Flutter ARB strings via arb_translate, then regenerates the
# localization classes (`flutter pub get` + `flutter gen-l10n`). Requires
# ARB_TRANSLATE_API_KEY in the environment or a .env file in --project-dir. If
# arb_translate isn't installed, either `dart pub global activate arb_translate`
# yourself, or point at a local fork with --activate-source.
appstoresuite translate-arb --project-dir /path/to/your-app

# Translates ARB strings, refreshes firestore.indexes.json from the deployed
# indexes, then deploys Firestore (rules + indexes) and Cloud Functions via the
# firebase CLI. For Flutter apps using Firebase as their backend.
appstoresuite ship-backend --project-dir /path/to/your-app
```

## Style options

`style:` in a config controls one consistent look for the whole app (not per-shot).
All fields are optional; omitting them keeps the original solid-background, upright,
undecorated look.

![Style preview: gradient and svg decoration modes, auto-derived per shot from each screenshot's own content](style_preview.png)

```yaml
style:
  background_color: "#FAFAF8"
  background_mode: "solid" # solid | auto | gradient
  gradient_color2: "#D8E6FF" # 2nd color for background_mode: gradient (top -> bottom).
  # Omit it and it's auto-derived per shot instead: a vivid accent color sampled from
  # that screenshot's own content (skipping white/gray UI chrome), pastel-lightened.
  title_color: "#1A1A1A"
  font_bold: "Poppins-Bold.ttf"
  font_regular: "Poppins-Regular.ttf"
  layout: "centered" # centered | tilted
  tilt_degrees: 6 # rotation for layout: tilted; alternates left/right per shot
  decoration: "none" # none | shapes | svg
  decoration_color: "#1A1A1A" # for decoration: shapes | svg. Omit it and it's
  # auto-derived the same way as gradient_color2 (falls back to title_color if the
  # screenshot has nothing vivid enough to sample).
  decoration_svg_dir: decorations # for decoration: svg — dir of .svg files, one picked
  # per shot deterministically, rasterized via CairoSVG and drawn behind the device at
  # low opacity. Path is relative to the config file's own directory.
```

Text color is also always auto-checked for contrast against whatever it actually sits
on (background + decoration) — `title_color` is kept if it already contrasts, otherwise
swapped for white-on-dark or near-black-on-light automatically.

`background_mode: auto` and `background_mode: gradient` are mutually exclusive —
`gradient` always uses `background_color`/`gradient_color2` and ignores per-shot
sampling. Tilt direction and svg/shape choice are both derived from a hash of the shot
id, so the same shot always renders the same way across devices and re-runs.

## How device frames work

Bezel images and screen-offset metadata come from
[fastlane/frameit-frames](https://github.com/fastlane/frameit-frames) (MIT), fetched
on demand and cached at `~/.cache/app-store-suite/frames`. The mapping from a
simulator/AVD name to a frame asset lives in `app_store_suite/devices.py`
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
