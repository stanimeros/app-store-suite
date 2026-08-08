# app-store-suite

Store-asset and shipping automation for Flutter apps: unattended screenshot capture
via deep links, framed/titled store-ready marketing images, the Play Store icon and
feature graphic, Google Play / App Store listing copy via the `claude` CLI, and
shipping builds to TestFlight / Play Store internal testing via fastlane.

Installs as a single global `appstoresuite` command (via pipx) so it runs from any
directory. It holds no per-app state itself — each app's config lives in that app's
own repo (see `templates/app_store_suite.example.yaml`), the same way `pubspec.yaml`
or `l10n.yaml` do.

## Install

```bash
cd app-store-suite
pipx install --editable .
```

`--editable` means pulling future changes in this repo (`git pull`) takes effect
immediately, without reinstalling. Upgrading after a `git pull`:

```bash
pipx upgrade app-store-suite
```

## Set up a new app

`appstoresuite init` (run from inside your Flutter project, or with `--project-dir`)
scaffolds the starter files most apps need: `app_store_suite.yaml`, `l10n.yaml` + a
template `lib/l10n/app_en.arb`, `.env.example`, a `fastlane/` skeleton (Fastfile
with empty `ship_testflight`/`ship_internal` lanes + Appfile), and
`lib/debug/screenshot_router.dart` — a template implementation of the debug router
`auto-capture` needs (see "Auto-capture requirements" below), full of `TODO`s and a
doc comment covering how to wire it up plus two gotchas worth reading before you
start (a splash-screen navigation race, and network images not being loaded yet when
a shot is captured). App name, iOS bundle id, and Android package name are
auto-detected from `pubspec.yaml`, `ios/Runner.xcodeproj/project.pbxproj`, and
`android/app/build.gradle(.kts)` and filled into the generated config/Appfile — pass
`--name` to override the detected app name. It never overwrites an existing file
unless you pass `--force`. Fastlane still needs `fastlane init` run separately
per-platform to wire up real Apple/Google credentials — the generated Fastfile is
just a lane-name-matching stub for you to fill in.

All scaffolded files are copied from `templates/` next to this package (source of
truth for `init` — edit them there if you want to change what new projects get).

Alternatively, copy `templates/app_store_suite.example.yaml` into your Flutter app's own repo root
(e.g. as `app_store_suite.yaml`, alongside `pubspec.yaml`), and fill in `app.name`,
`icon_source`, your devices, and (if you want `auto-capture`) `deep_link_scheme` +
`shots:` — see "Auto-capture requirements" below. `flutter_dir: .` assumes the config
sits at the repo root; adjust if not. Add `.appstoresuite/` to that app's own
`.gitignore` — it's where all generated output (raw + composed screenshots, listing
copy, style choices) is written, next to the config.

If a device identifier isn't already in `app_store_suite/devices.py`'s `FRAME_MAP`,
either add a frame mapping there or accept the procedural rounded-corner fallback
frame `compose.py` uses instead.

## Usage

Every command takes `--config` pointing at wherever you put that app's config.

```bash
# One-time: create any missing Android AVDs, cache device bezel frames.
appstoresuite setup --config /path/to/your-app/app_store_suite.yaml

# Unattended capture: boots each configured device, opens each configured shot's
# deep link, screenshots it, tears the device down again. See "Auto-capture
# requirements" below for what the app itself needs to expose.
appstoresuite auto-capture --config /path/to/your-app/app_store_suite.yaml
appstoresuite auto-capture --config /path/to/your-app/app_store_suite.yaml --device ios_phone
appstoresuite auto-capture --config /path/to/your-app/app_store_suite.yaml --render-delay 8

# For apps whose debug router also reads a `lang` deep-link query param to switch
# the in-app language (see "Auto-capture requirements" below): captures raw shots
# under raw/<lang>/<device>/ instead of the shared raw/<device>/, so re-running per
# language doesn't clobber the previous language's captures.
appstoresuite auto-capture --config /path/to/your-app/app_store_suite.yaml --lang en
appstoresuite auto-capture --config /path/to/your-app/app_store_suite.yaml --lang el

# Comma-separated languages capture back-to-back in one boot/launch per device
# instead of one full boot per language — faster, and (on iOS) only has to get
# past the "Open in <App>?" dialog once per device instead of once per language.
appstoresuite auto-capture --config /path/to/your-app/app_store_suite.yaml --lang en,el

# Composites .appstoresuite/raw/<device>/<shot>.png into
# .appstoresuite/<lang>/store/<device>/<shot>.png: device bezel frame (or the
# procedural fallback), background, and the shot's title.
appstoresuite compose --config /path/to/your-app/app_store_suite.yaml

# 512x512 Play Store app icon, resized from app.icon_source.
appstoresuite store-icon --config /path/to/your-app/app_store_suite.yaml

# 1024x500 Play Store feature graphic.
appstoresuite feature-graphic --config /path/to/your-app/app_store_suite.yaml --headline "Plan every trip"

# Drafts "proposed" Google Play / App Store listing copy (app name, descriptions,
# keywords) from the shots' titles/subtitles via the `claude` CLI, written into
# .appstoresuite/<lang>/store_listing.json alongside "current" (see below).
# Character counts against each store's limits are computed in Python, not
# trusted from the model's own output.
appstoresuite store-listing --config /path/to/your-app/app_store_suite.yaml

# Fetches the currently-live listing copy from App Store Connect (fastlane deliver)
# and/or Play Console (fastlane supply) into "current" in the same JSON file,
# leaving "proposed" untouched. Requires store credentials in the config (below).
appstoresuite fetch-listing --config /path/to/your-app/app_store_suite.yaml

# Translate one language's shot titles/subtitles into another via the `claude` CLI
# (separate from the app's own ARB strings — see "Auto-capture requirements" above).
appstoresuite translate-titles --config /path/to/your-app/app_store_suite.yaml --from en --to el
```

## Auto-capture requirements

Unattended capture drives the app itself via deep link — no manual navigation, no
interactive prompts. For the target app to support it, it needs to expose three
things:

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

ARB/localization strings aren't part of this contract — auto-capture runs the app in
whatever locale the device/simulator is already set to. Composed titles/subtitles are
generated separately per shot by `store-listing`/`translate-titles`, stored in
`.appstoresuite/<lang>/titles.json`, independent of the app's own ARB files.

Without `deep_link_scheme` + `shots:` configured, `auto-capture` refuses to run
(fails fast with what's missing) — every other command still works.

**iOS "Open in *App*?" dialog.** The iOS Simulator shows a system confirmation
sheet on *every* `simctl openurl` of a custom URL scheme (it doesn't
distinguish a real external app/Safari from automation, and — unlike the
one-time prompt you'd see on a real device — it does not stay dismissed for
the rest of an app session; it reappears on each call). `open_url` in
`capture/ios.py` detects it (via System Events, so it needs Accessibility
access — macOS prompts for this automatically the first time; grant it under
System Settings > Privacy & Security > Accessibility if it was previously
denied) and pauses capture with a printed prompt until you tap **Open**
yourself in the Simulator window; it resumes on its own the moment the dialog
is gone. This is also where you'd handle a real sign-in prompt if a shot's
route ever needs one — same pause-and-wait, no separate mechanism. If System
Events can't see the dialog at all (no Accessibility access), capture just
proceeds after a short settle delay and that shot's screenshot shows the
dialog instead of the target screen — grant access and re-run rather than
trying to work around it by re-running multiple times.

## Listing metadata: current vs. proposed

`.appstoresuite/<lang>/store_listing.json` holds one entry per field (app name,
descriptions, keywords, etc.), each with a `current` value (what's actually live,
pulled by `fetch-listing`) and a `proposed` value (a draft, from `store-listing` or
hand-edited). Nothing is ever uploaded automatically — copy `proposed` into App Store
Connect / Play Console's own listing forms yourself once you're happy with it.

`fetch-listing` needs store credentials in the config — reuse whatever your Fastfile
already uses for shipping:

```yaml
app:
  bundle_id: com.yourcompany.yourapp
  asc_key_id: XXXXXXXXXX
  asc_issuer_id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  asc_key_path: keys/AuthKey_XXXXXXXXXX.p8 # relative to flutter_dir
  android_package_name: com.yourcompany.yourapp
  play_json_key: keys/your-service-account.json # relative to flutter_dir
```

Store locale codes don't always match your own language codes (e.g. Play uses
`el-GR` for Greek where App Store Connect just uses `el`) — `fetch-listing` tries
your code as-is first, then a couple of common variants for `en`. If it can't find a
match it fails with the locale codes it actually found, so you can add an override:

```yaml
store_locales:
  el: el-GR
```

`fetch-listing` runs `fastlane deliver`/`fastlane supply` in a scratch
`fastlane/metadata/` directory inside the app's repo (their standard working
format — dozens of per-field `.txt` files); app-store-suite reads what it needs
from there into the JSON and deletes that scratch directory again immediately,
so it doesn't linger as clutter.

## Pushing metadata and screenshots live

`store-listing`/`compose` only ever write local files — nothing reaches the
stores until you push it. `push` does that, deliberately scoped to *just*
metadata text, screenshots, and/or (Play only) the store icon + feature
graphic (never a binary — use `ship-ios`/`ship-android` for that, and never
anything you haven't reviewed locally first):

```bash
# Everything (metadata text + screenshots + Play images), both stores, all configured languages.
appstoresuite push --config /path/to/your-app/app_store_suite.yaml

# Just one platform/target/language.
appstoresuite push --config /path/to/your-app/app_store_suite.yaml --platform android --what metadata
appstoresuite push --config /path/to/your-app/app_store_suite.yaml --platform ios --what screenshots --lang en,el
appstoresuite push --config /path/to/your-app/app_store_suite.yaml --platform android --what images
```

`--what metadata` pushes each language's *proposed* copy from
`store_listing.json` (see above) — draft it with `store-listing` and review it
before pushing; nothing here asks for confirmation. `--what screenshots`
pushes whatever `compose` last wrote to `.appstoresuite/<lang>/store/`. `--what
images` pushes the store icon (`store-icon`) and each language's feature
graphic (`feature-graphic`) — Play only, since App Store Connect has no
equivalent upload slot (the app icon ships inside the binary there); it's a
no-op for `--platform ios`.

Requires the same store credentials as `fetch-listing` (above). Android
tablet screenshots (any device key containing `"tablet"`) go to both Play's
sevenInch and tenInch buckets, since a single composed image can't target
both; iOS screenshots are auto-bucketed by App Store Connect from each
image's pixel dimensions, so no per-device mapping is needed there.

The three bugs below all live inside fastlane's own Ruby (`deliver`), not
anything of ours — `appstoresuite setup` patches the installed gem for all
three automatically (idempotent, safe to re-run any time, e.g. after a
fastlane upgrade — see `gem_patches.py`). Documented here for the "why", and
in case you're on a fastlane version where the patch text doesn't match
(`setup` will tell you if a patch was skipped for that reason).

**Known fastlane bug (fastlane 2.237.0):** `push --platform ios --what
metadata` calls `fastlane deliver`, which can crash with
`Spaceship::ConnectAPI::Models.parse: No data` on apps that have never had an
App Store version reviewed yet (deliver's `review_attachment_file` step fetches
`app_store_review_detail`, which 404s and isn't rescued — unlike the identical
case just above it in the same file, which is). `setup` patches the installed
gem: in `deliver/lib/deliver/upload_metadata.rb`'s `review_attachment_file`,
wraps `version.fetch_app_store_review_detail` in `begin/rescue; nil; end` and
adds `return unless app_store_review_detail` right after, mirroring the
existing `fetch_reset_ratings_request` rescue a few lines up.

**Known fastlane bug, Play side:** every Android push passes
`--changes_not_sent_for_review true`, meant to leave the edit as an
unpublished draft in Play Console. For edits that only touch listing content
(metadata/images/screenshots, no APK/AAB) rather than a track/release, Google's
backend appears to ignore the flag and sends it for review anyway — see
[fastlane/fastlane#26439](https://github.com/fastlane/fastlane/issues/26439),
same scenario. Not fixable from here; judged an acceptable tradeoff since
Play's review of listing content is normally fast/automated, unlike binary
review — just don't expect the draft-hold to actually work.

**Known fastlane bug, iOS screenshots — duplicates:** `push --platform ios
--what screenshots` can leave duplicate screenshots on App Store Connect, even
though `push_ios_screenshots` already isolates each language into its own
`deliver` call specifically to reduce this. Root cause, traced in
`deliver/lib/deliver/upload_screenshots.rb`: after uploading, deliver polls
Apple's processing state, then verifies every local file made it up by
checksum. If Apple hasn't finished processing a screenshot by the time that
check runs (a few seconds later), it looks "missing" even though it actually
succeeded, and deliver retries. The retry's duplicate-check compares against
`app_screenshot_set.app_screenshots`, a snapshot cached before the *first*
upload batch and never refreshed — so it doesn't recognize the screenshots
that just succeeded, and re-uploads them as genuinely new entries. Which
files get hit is non-deterministic (depends on Apple's processing speed that
run). Not exposed as any `deliver`/CLI option — `setup` patches the installed
gem: in `deliver/lib/deliver/upload_screenshots.rb`'s `upload_screenshots`,
right before the `iterator.each_local_screenshot` loop, adds a
`refreshed_sets = {}` memo hash, then at the top of that loop's block:

```ruby
unless refreshed_sets[app_screenshot_set.id]
  fresh_set = Spaceship::ConnectAPI::AppScreenshotSet.get(app_screenshot_set_id: app_screenshot_set.id)
  app_screenshot_set.app_screenshots = fresh_set.app_screenshots if fresh_set
  refreshed_sets[app_screenshot_set.id] = true
end
```

This re-fetches each screenshot set's current state fresh once per call
(including retries) instead of trusting the stale cache, so the duplicate
check actually sees what's really on App Store Connect. If you already have
duplicates from before this was patched: delete the affected screenshots in
App Store Connect and push again (the delete step at the start of every push
unconditionally clears a locale's entire existing screenshot set first, so
you don't need to hand-pick which ones to remove).

Even with that fix, a retry can still fire on a **false positive** — Apple
hadn't finished processing yet, nothing was actually wrong — and each retry
is still a real second upload pass, just a deduped one now. Given the choice
between "occasionally reports a stale processing state it doesn't wait out"
and "ever risks a duplicate," this project's `upload_screenshots.rb` also has
the retry itself disabled: `retry_upload_screenshots_if_needed`'s `else`
branch (the "Tries remaining" one) no longer calls `upload_screenshots`
again — it just logs what it saw (failure/still-processing/missing) and
returns. One upload pass, full stop; if Apple was still processing, checking
App Store Connect a minute later than the CLI exiting always shows it caught
up. If you're setting this up fresh, apply both patches together.

## Shipping

These operate directly on a Flutter project checkout. `--project-dir` is optional:
pass `--config` instead to derive it from that config's `flutter_dir`, or omit both
and run from inside the Flutter project itself (detected via `pubspec.yaml`).

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

Use `style-preview`/`style-pick` to compare variants and pin one per shot:

```bash
appstoresuite style-preview --config /path/to/your-app/app_store_suite.yaml
appstoresuite style-pick --config /path/to/your-app/app_store_suite.yaml --shot home --variant gradient
appstoresuite style-pick --config /path/to/your-app/app_store_suite.yaml --list
```

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

`auto-capture` always launches the app with plain `flutter run` (debug mode) — the
iOS Simulator can't run release/profile builds, only physical devices can. Make sure
your app sets `debugShowCheckedModeBanner: false` on `MaterialApp` so the red DEBUG
ribbon doesn't show up in screenshots.
