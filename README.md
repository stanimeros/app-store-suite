# app-store-suite

Store-asset and shipping automation for Flutter apps: drives each configured
simulator/emulator through a fixed list of shots via deep links (`auto-capture`, no
manual navigation), composites the raw captures into framed, titled, store-ready
marketing images, generates the 512x512 Play Store app icon, the 1024x500 Play Store
feature graphic, and Google Play / App Store listing copy via the `claude` CLI — and
ships builds to TestFlight / Play Store internal testing via fastlane.

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
# ARB_TRANSLATE_API_KEY in the environment or a .env file in --project-dir.
# arb_translate itself is vendored at vendor/arb_translate (a fork of
# https://github.com/leancodepl/arb_translate) and gets activated automatically
# the first time it's needed — pass --activate-source to use a different fork.
appstoresuite translate-arb --project-dir /path/to/your-app
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

## Auto-capture requirements

`capture` (and the web UI) are interactive: you navigate the running app by hand,
then name and snap each screen yourself. `auto-capture` replaces that with a fixed
list of shots the tool drives itself — no navigation, no typing. For the target app
to support it, it needs to expose three things:

1. **A deep-link scheme.** Declare it in the config:

   ```yaml
   app:
     deep_link_scheme: chronal # chronal://<route>
   ```

   The app must register that scheme (iOS: `CFBundleURLSchemes` in `Info.plist`;
   Android: an `<intent-filter>` with `android:scheme="chronal"` on the launcher
   activity) and be able to handle it while cold-starting or already running.

2. **A fixed shot list with stable keys**, one entry per screen you want captured,
   in the config:

   ```yaml
   shots:
     - id: home
       route: shot/home
     - id: trip_map
       route: shot/trip-map
   ```

   `id` is the filename shots are saved/composed under (must stay stable across
   runs — renaming it starts that shot over with a fresh AI-suggested title).
   `route` is whatever your app's router expects after the `scheme://`.

3. **A debug router that lands on each route with sample data already loaded** —
   no login, no live network calls, no dependency on real user state. A route
   handler should short-circuit straight to the target screen with mock/sample
   data injected (chronal does this for trip previews already — reuse that
   pattern: a `shot/<screen>` route maps to the same screen a normal navigation
   would reach, but seeded with a canned sample trip instead of requiring the
   user to have actually created one). Keep this behind a debug-only build flag
   if the scheme shouldn't be reachable in production.

ARB/localization strings aren't part of this contract — `auto-capture` runs the
app in whatever locale the device/simulator is already set to, same as `capture`.
Composed titles/subtitles are still generated separately per shot by
`store-listing`/`translate-titles`, stored in `output/<app>/<lang>/titles.json`,
independent of the app's own ARB files.

```bash
appstoresuite auto-capture --config /path/to/your-app/configs/app.yaml
appstoresuite auto-capture --config /path/to/your-app/configs/app.yaml --device ios_phone
appstoresuite auto-capture --config /path/to/your-app/configs/app.yaml --render-delay 3
```

## Adding a new app

Copy `configs/chronal.yaml`, point `app.flutter_dir` / `icon_source` at the new
project, and list its shots. If you add a device identifier not already in
`FRAME_MAP`, either add a frame mapping in `devices.py` or accept the procedural
fallback frame. Add `deep_link_scheme` + `shots:` too if you want `auto-capture`
to work for it (see above) — otherwise `capture`/the web UI still work with
no fixed shot list, naming screens as you go.
