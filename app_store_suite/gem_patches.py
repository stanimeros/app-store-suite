from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Patch:
    name: str
    gem_relpath: str  # relative to the fastlane gem's root dir
    old: str
    new: str


# Each fixes a real bug hit while using `push`/`ship` against real App Store
# Connect/Play Console accounts — see README's "Known fastlane bug" notes for
# the full story on each. All three are idempotent (skipped if `new` is
# already present) and only touch `deliver`'s bundled Ruby, never anything of
# ours, so they're safe to re-run any time (e.g. after a fastlane upgrade).
PATCHES = [
    Patch(
        name="deliver metadata crash on apps with no reviewed version",
        gem_relpath="deliver/lib/deliver/upload_metadata.rb",
        old=(
            "    def review_attachment_file(version)\n"
            "      app_store_review_detail = version.fetch_app_store_review_detail\n"
        ),
        new=(
            "    def review_attachment_file(version)\n"
            "      app_store_review_detail = begin\n"
            "                                   version.fetch_app_store_review_detail\n"
            "                                 rescue\n"
            "                                   nil\n"
            "                                 end # returns no data error so need to rescue\n"
            "      return unless app_store_review_detail\n"
        ),
    ),
    Patch(
        name="deliver screenshot duplicate uploads (stale cache on retry)",
        gem_relpath="deliver/lib/deliver/upload_screenshots.rb",
        old=(
            "      iterator = AppScreenshotIterator.new(localizations)\n"
            "      iterator.each_local_screenshot(screenshots_per_language) do |localization, app_screenshot_set, screenshot|\n"
            "        # Initialize counter on each app screenshot set\n"
            "        number_of_screenshots_per_set[app_screenshot_set] ||= (app_screenshot_set.app_screenshots || []).count\n"
        ),
        new=(
            "      refreshed_sets = {}\n"
            "\n"
            "      iterator = AppScreenshotIterator.new(localizations)\n"
            "      iterator.each_local_screenshot(screenshots_per_language) do |localization, app_screenshot_set, screenshot|\n"
            "        unless refreshed_sets[app_screenshot_set.id]\n"
            "          fresh_set = Spaceship::ConnectAPI::AppScreenshotSet.get(app_screenshot_set_id: app_screenshot_set.id)\n"
            "          app_screenshot_set.app_screenshots = fresh_set.app_screenshots if fresh_set\n"
            "          refreshed_sets[app_screenshot_set.id] = true\n"
            "        end\n"
            "\n"
            "        # Initialize counter on each app screenshot set\n"
            "        number_of_screenshots_per_set[app_screenshot_set] ||= (app_screenshot_set.app_screenshots || []).count\n"
        ),
    ),
    Patch(
        name="deliver screenshot upload never retries (single pass only)",
        gem_relpath="deliver/lib/deliver/upload_screenshots.rb",
        old=(
            "      if tries.zero?\n"
            "        iterator.each_app_screenshot.select { |_, _, app_screenshot| app_screenshot.error? }.each do |localization, _, app_screenshot|\n"
            "          UI.error(\"#{app_screenshot.file_name} for #{localization.locale} has error(s) - #{app_screenshot.error_messages.join(', ')}\")\n"
            "        end\n"
            "        incomplete_screenshot_count = states.except('COMPLETE').reduce(0) { |sum, (k, v)| sum + v }\n"
            "        UI.user_error!(\"Failed verification of all screenshots uploaded... #{incomplete_screenshot_count} incomplete screenshot(s) still exist\")\n"
            "      else\n"
            "        UI.error(\"Failed to upload all screenshots... Tries remaining: #{tries}\")\n"
            "        # Delete bad entries before retry\n"
            "        iterator.each_app_screenshot do |_, _, app_screenshot|\n"
            "          app_screenshot.delete! unless app_screenshot.complete?\n"
            "        end\n"
            "        upload_screenshots(localizations, screenshots_per_language, timeout_seconds, tries: tries)\n"
            "      end\n"
        ),
        new=(
            "      # PATCH (app-store-suite): this retry path is what caused duplicate\n"
            "      # screenshots in the first place — it re-invokes upload_screenshots,\n"
            "      # and even with the staleness fix above, a false \"missing\" verdict\n"
            "      # here (Apple just hadn't finished processing yet, nothing's actually\n"
            "      # wrong) triggers a real second pass. A single upload attempt that\n"
            "      # sometimes reports a spurious \"still processing\" is a much smaller\n"
            "      # problem than a real risk of duplicate screenshots on every push, so\n"
            "      # this no longer retries at all — just reports what it saw and stops.\n"
            "      UI.important(\n"
            "        \"Screenshot verification reported issues (failure=#{is_failure}, \"\\\n"
            "        \"still-processing=#{is_processing}, missing=#{is_missing_screenshot}) \"\\\n"
            "        \"but not retrying (patched) to avoid duplicate uploads — if Apple was \"\\\n"
            "        \"still processing, check App Store Connect directly; it usually \"\\\n"
            "        \"finishes shortly after this run exits.\"\n"
            "      )\n"
            "    end\n"
        ),
    ),
]


def _fastlane_gem_dir(flutter_dir: Path) -> Path | None:
    """Resolves the installed fastlane gem's root dir via the project's own
    Gemfile/bundler environment (so this matches whatever version `bundle
    exec fastlane` actually runs, not just whatever's newest on the system).
    """
    try:
        result = subprocess.run(
            ["bundle", "exec", "ruby", "-e", "print Gem::Specification.find_by_name('fastlane').gem_dir"],
            cwd=flutter_dir, capture_output=True, text=True, check=True,
        )
    except Exception:
        return None
    path = Path(result.stdout.strip())
    return path if path.is_dir() else None


def apply_known_patches(flutter_dir: Path) -> list[str]:
    """Applies every patch in PATCHES that isn't already applied. Returns a
    list of human-readable status lines (applied / already applied / gem
    text didn't match — probably a different fastlane version, skipped)."""
    gem_dir = _fastlane_gem_dir(flutter_dir)
    if gem_dir is None:
        return ["Could not resolve the installed fastlane gem (is `bundle install` run in this project?) — skipped"]

    results = []
    for patch in PATCHES:
        target = gem_dir / patch.gem_relpath
        if not target.exists():
            results.append(f"{patch.name}: {target} not found — skipped")
            continue
        text = target.read_text()
        if patch.new in text:
            results.append(f"{patch.name}: already applied")
        elif patch.old in text:
            target.write_text(text.replace(patch.old, patch.new, 1))
            results.append(f"{patch.name}: applied")
        else:
            results.append(
                f"{patch.name}: expected text not found in {target.name} "
                "(different fastlane version?) — skipped, patch it by hand if you hit this bug"
            )
    return results
