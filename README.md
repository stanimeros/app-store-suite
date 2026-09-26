# app-store-suite

Store-asset and release automation for Flutter apps, as a single CLI:

- **Capture** — unattended screenshots of every screen, driven by deep links.
- **Compose** — device-framed, titled, store-ready marketing images.
- **Assets** — Play Store app icon and feature graphic.
- **Copy** — draft App Store / Play Store listing text via the `claude` CLI.
- **Push** — upload metadata, screenshots and images; ship builds via fastlane.

It holds no per-app state. Each app's config lives in that app's own repo, the
same way `pubspec.yaml` and `l10n.yaml` do.

## Requirements

- Python 3.10+ and [pipx](https://pipx.pypa.io)
- Flutter, Xcode (iOS), Android SDK (Android)
- Ruby + [fastlane](https://fastlane.tools) for `push` / `ship-*`
- `OPENAI_API_KEY` for AI backgrounds, `claude` CLI for listing copy

## Install

```bash
git clone <this repo> && cd app-store-suite
pipx install --editable .
```

`--editable` means a `git pull` takes effect immediately. If you ever need to
force a refresh: `pipx upgrade app-store-suite`.

## Quick start

```bash
cd /path/to/your-flutter-app

# 1. Scaffold config + fastlane skeleton + debug screenshot router.
appstoresuite init

# 2. Fill in app_store_suite.yaml (devices, shots, deep_link_scheme), then:
appstoresuite setup      --config app_store_suite.yaml   # AVDs, device frames, fastlane patches
appstoresuite auto-capture --config app_store_suite.yaml # raw screenshots
appstoresuite generate-bgs --config app_store_suite.yaml # AI backgrounds
appstoresuite bg-pick    --config app_store_suite.yaml --shot home --set 1
appstoresuite compose    --config app_store_suite.yaml   # framed store images

# 3. Store assets and copy.
appstoresuite store-icon      --config app_store_suite.yaml
appstoresuite feature-graphic --config app_store_suite.yaml --headline "Plan every trip"
appstoresuite store-listing   --config app_store_suite.yaml

# 4. Review `git diff`, then upload.
appstoresuite push --config app_store_suite.yaml
```

Every asset command takes `--config`. Shipping commands take `--project-dir`
instead (or `--config`, or nothing when run from inside the Flutter project).

## Commands

| Command | What it does |
| --- | --- |
| `init` | Scaffold config, `l10n.yaml`, `.env.example`, fastlane skeleton, debug router |
| `setup` | Create missing AVDs, cache device frames, patch fastlane (see below) |
| `auto-capture` | Boot each device, open each shot's deep link, screenshot, tear down |
| `compose` | Frame + brand raw captures into the real fastlane output paths |
| `generate-bgs` / `bg-pick` | Generate and pin AI screenshot backgrounds |
| `store-icon` | 512×512 Play Store icon from `app.icon_source` |
| `feature-graphic` | 1024×500 Play Store feature graphic |
| `generate-fg-bg` / `fg-bg-pick` | Generate and pin the feature graphic background |
| `store-listing` | Draft listing copy (name, descriptions, keywords) via `claude` |
| `fetch-listing` | Pull the currently-live listing copy down as a git baseline |
| `translate-titles` | Translate shot titles/subtitles into another language |
| `validate` | Flag over-limit and near-duplicate listing fields |
| `push` | Upload metadata / screenshots / Play images — never a binary |
| `bump-version` | Bump `pubspec.yaml`'s PATCH and +BUILD together |
| `ship-ios` / `ship-android` | Build and upload to TestFlight / Play internal testing |
| `translate-arb` | Translate missing Flutter ARB strings, regenerate l10n classes |

Run `appstoresuite <command> --help` for every flag.

## Config

`init` writes `app_store_suite.yaml` into your app repo, auto-detecting the app
name, iOS bundle id and Android package from `pubspec.yaml`,
`project.pbxproj` and `build.gradle`. See
[`templates/app_store_suite.example.yaml`](templates/app_store_suite.example.yaml)
for the annotated full version. The minimum:

```yaml
app:
  name: YourApp
  flutter_dir: .                      # relative to this file
  icon_source: assets/icon/icon.png   # >= 1024x1024
  deep_link_scheme: myapp             # only needed for auto-capture

languages: [en]                       # first is the default

devices:
  ios_phone:
    simulator: "iPhone 17 Pro"        # from `xcrun simctl list devices available`
  android_phone:
    avd: "Medium_Phone"               # `setup` creates missing AVDs

shots:                                # only needed for auto-capture
  - id: home
    route: shot/home
```

Store credentials (needed for `push` and `fetch-listing`) go in the same file —
reuse whatever your Fastfile already uses:

```yaml
app:
  bundle_id: com.yourcompany.yourapp
  asc_key_id: XXXXXXXXXX
  asc_issuer_id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  asc_key_path: keys/AuthKey_XXXXXXXXXX.p8      # relative to flutter_dir
  android_package_name: com.yourcompany.yourapp
  play_json_key: keys/your-service-account.json # relative to flutter_dir
```

Key files and `.env` belong in your app's repo and should stay gitignored.

Where store locale codes differ from your language codes (Play uses `el-GR`
where App Store Connect uses `el`), add overrides — otherwise your code is
tried as-is first, then a few common variants:

```yaml
store_locales:
  el: el-GR
```

`init` also scaffolds two dependency-hygiene defaults: `license_checker.yaml`
(a starter license allow/reject policy) and `pubspec_additions.yaml` (a snippet
to merge into `pubspec.yaml` by hand — `init` never edits an existing
`pubspec.yaml`). Everything is copied from [`templates/`](templates); edit there
to change what new projects get. Nothing is overwritten without `--force`.

### Style

`style:` sets one consistent look for the whole app. All fields are optional.

```yaml
style:
  title_color: "#1A1A1A"
  background_color: "#FAFAF8"   # feature-graphic only, not compose
  font_bold: "Poppins-Bold.ttf"
  font_regular: "Poppins-Regular.ttf"
  layout: "centered"            # centered | tilted
  tilt_degrees: 6               # for layout: tilted; alternates per shot
```

`title_color` is kept only if it contrasts with the background; otherwise it's
swapped for white-on-dark or near-black-on-light automatically. Tilt direction
is derived from a hash of the shot id, so a shot renders identically across
devices and re-runs.

## Auto-capture requirements

Capture drives the app by deep link — no manual navigation, no prompts. Your
app needs three things:

1. **A registered URL scheme.** iOS: `CFBundleURLSchemes` in `Info.plist`.
   Android: an `<intent-filter>` with `android:scheme="myapp"` on the launcher
   activity. It must work on cold start and while already running.

2. **A stable shot list** in the config. `id` is the filename shots are saved
   under (renaming it starts that shot over with a fresh AI title); `route` is
   whatever your router expects after `scheme://`.

3. **A debug router that lands on each route with sample data already loaded** —
   no login, no live network calls, no dependency on real user state. A
   `shot/<screen>` route should short-circuit to the same screen a normal
   navigation reaches, but seeded with canned mock data. `init` scaffolds
   [`lib/debug/screenshot_router.dart`](templates/lib/debug/screenshot_router.dart)
   as a starting point, with TODOs and two gotchas worth reading first (a
   splash-screen navigation race, and network images not being loaded yet when a
   shot is captured). Keep the scheme behind a debug-only flag if it shouldn't
   be reachable in production.

Without `deep_link_scheme` and `shots:`, `auto-capture` fails fast with what's
missing; every other command still works.

Localization isn't part of this contract — the app runs in whatever locale the
device is set to. If your router reads a `lang` query param, pass `--lang` to
capture per language into `raw/<lang>/<device>/`; comma-separated values
(`--lang en,el`) capture back-to-back in one boot per device. Composed
titles/subtitles are separate from your ARB files and live in
`fastlane/appstoresuite/<lang>/titles.json`.

Capture always uses plain `flutter run` (debug mode) — the iOS Simulator can't
run release or profile builds. Set `debugShowCheckedModeBanner: false` on
`MaterialApp` so the red DEBUG ribbon stays out of your screenshots.

**iOS "Open in *App*?" dialog.** The Simulator shows a confirmation sheet on
*every* `simctl openurl` of a custom scheme, and it doesn't stay dismissed.
`capture/ios.py` detects it via System Events and pauses until you tap **Open**
yourself, then resumes on its own. This needs Accessibility access (macOS
prompts the first time; otherwise grant it under System Settings → Privacy &
Security → Accessibility). Without it, capture proceeds after a short delay and
that shot captures the dialog instead of your screen — grant access and re-run.
This is also where a real sign-in prompt gets handled, if a route ever needs one.

## Where files go

```
fastlane/
  appstoresuite/          # this tool's working data — commit it
    raw/                  #   raw captures (expensive to redo)
    backgrounds/set<N>/   #   generated background candidates
    <lang>/titles.json    #   shot titles/subtitles
    contact_sheet_<lang>.png
  screenshots/<locale>/                          # store-facing (iOS)
  metadata/<platform>/<locale>/*.txt             # store-facing (listing copy)
  metadata/android/<locale>/images/<category>/   # store-facing (Android)
```

`appstoresuite/` is nested inside `fastlane/` but isn't something fastlane or
the stores consume. Commit it rather than gitignoring it — raw captures require
a real device run to reproduce. Everything store-facing is written straight into
the layout `fastlane deliver` / `fastlane supply` already expect, so commit that
too.

## Backgrounds

Composed screenshot backgrounds come from `generate-bgs`, which sends each
shot's raw screenshot to the OpenAI images API and asks for a simple background
(soft shapes and gradients drawn from that screenshot's own colors, no text, no
UI) to sit behind the framed device and title. There is no solid-color fallback:
`compose` skips any shot with no background chosen and prints the command to run.

Each run writes exactly one new numbered set, never overwriting previous ones —
one per call by design, so you review before generating another. Nothing is
picked automatically:

```bash
appstoresuite generate-bgs --config app_store_suite.yaml
appstoresuite bg-pick --config app_store_suite.yaml --shot home --set 1

# Not happy with it? Generate another set for just that shot, then re-pick.
appstoresuite generate-bgs --config app_store_suite.yaml --shot home
appstoresuite bg-pick --config app_store_suite.yaml --shot home --set 2
appstoresuite bg-pick --config app_store_suite.yaml --list
```

Adjust `BACKGROUND_PROMPT` in `backgrounds.py` to steer the look. The feature
graphic has its own equivalent pair, `generate-fg-bg` / `fg-bg-pick`, which uses
the app icon as its visual reference and falls back to `style.background_color`.

## Listing copy: current vs. proposed

Each listing field is a plain text file at its real fastlane path
(`fastlane/metadata/ios/<locale>/name.txt`,
`fastlane/metadata/android/<locale>/title.txt`, …) — the same filenames fastlane
itself reads. There's no separate model: **current** is what's committed to that
path in git, **proposed** is the working-tree version. So
`git diff -- fastlane/metadata/` *is* the comparison, and
`git show HEAD:fastlane/metadata/ios/en-US/name.txt` shows just the live value.

`store-listing` drafts proposed copy from the shots' titles via the `claude`
CLI; character counts are computed in Python, not trusted from the model.
`fetch-listing` pulls the live copy down and commits it as a baseline, so a
later diff shows only your own edits.

Nothing is ever uploaded automatically.

## Pushing live

`push` uploads only listing text, screenshots and (Play only) the store icon +
feature graphic. Never a binary — use `ship-ios` / `ship-android` for that.

```bash
# Everything, both stores, all configured languages.
appstoresuite push --config app_store_suite.yaml

# Or scope it.
appstoresuite push --config app_store_suite.yaml --platform android --what metadata
appstoresuite push --config app_store_suite.yaml --platform ios --what screenshots --lang en,el
appstoresuite push --config app_store_suite.yaml --platform android --what images
```

Review your diff first — `push` asks for no confirmation. `--what images` is a
no-op on iOS, where the app icon ships inside the binary. Android tablet
screenshots (any device key containing `tablet`) go to both Play's sevenInch and
tenInch buckets; iOS screenshots are auto-bucketed by App Store Connect from
their pixel dimensions.

### Fastlane patches

Three bugs live in fastlane's own `deliver` Ruby, not in this project.
`appstoresuite setup` patches the installed gem for all of them — idempotent and
safe to re-run, e.g. after a fastlane upgrade. It tells you if a patch was
skipped because the text didn't match your fastlane version. Patch bodies and
full rationale are in [`gem_patches.py`](app_store_suite/gem_patches.py).

1. **Metadata crash (fastlane 2.237.0).** `deliver`'s `review_attachment_file`
   fetches `app_store_review_detail`, which 404s on apps that never had a
   version reviewed, and isn't rescued — so `push --what metadata` dies with
   `Spaceship::ConnectAPI::Models.parse: No data`. The patch rescues it, exactly
   as the identical case a few lines above already does.

2. **Duplicate iOS screenshots.** After uploading, `deliver` verifies each local
   file by checksum. Screenshots Apple hasn't finished processing look
   "missing", triggering a retry — and the retry's duplicate check compares
   against a snapshot cached before the first batch, so it re-uploads them as
   new. The patch re-fetches each screenshot set's real state once per call.
   Since a retry is still a second upload pass even when deduped, the retry
   itself is also disabled: one upload pass, full stop. If Apple was still
   processing, App Store Connect catches up a minute after the CLI exits. Apply
   both patches together. To clean up pre-existing duplicates, delete the
   affected screenshots in App Store Connect and push again — every push clears
   a locale's entire screenshot set first.

3. **Play drafts get reviewed anyway.** Android pushes pass
   `--changes_not_sent_for_review true` to leave an unpublished draft, but for
   content-only edits Google's backend appears to ignore it
   ([fastlane#26439](https://github.com/fastlane/fastlane/issues/26439)). Not
   fixable from here, and acceptable since Play reviews listing content quickly
   — just don't rely on the draft hold.

## Device frames

Bezel images and screen-offset metadata come from
[fastlane/frameit-frames](https://github.com/fastlane/frameit-frames) (MIT),
fetched on demand and cached in `~/.cache/app-store-suite/frames`. The
simulator/AVD → frame mapping is `FRAME_MAP` in
[`devices.py`](app_store_suite/devices.py); frameit's coverage skews toward real
hardware names, so lookup is by exact identifier — add an entry for a new
device. If nothing matches (no modern Android tablet frame exists upstream, for
instance), `compose` falls back to a clean procedural rounded-corner frame with
a shadow rather than failing.

## License

MIT — see [LICENSE](LICENSE).

Third-party components keep their own licenses:

- [`vendor/arb_translate`](vendor/arb_translate) — a fork of
  [arb_translate](https://github.com/leancodepl/arb_translate) (Apache-2.0),
  used by `translate-arb` and activated automatically on first use. Pass
  `--activate-source` to use a different fork.
- Bundled fonts (Inter, Poppins, Noto Sans, Playpen Sans, Arima, Source Serif 4)
  — SIL Open Font License, see
  [`app_store_suite/fonts/OFL.txt`](app_store_suite/fonts/OFL.txt).
- Device frames from
  [fastlane/frameit-frames](https://github.com/fastlane/frameit-frames) (MIT),
  fetched at runtime rather than vendored.
