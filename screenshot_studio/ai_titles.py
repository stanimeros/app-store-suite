from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROMPT_TEMPLATE = (
    "Look at the screenshot image at {image_path}. This is a screen from the Flutter "
    "app '{app_name}'. Write App Store screenshot marketing copy for it: a short punchy "
    "title (max 6 words) and a one-sentence subtitle (max 12 words) describing the "
    "feature shown. This same copy will be reused across iOS and Android, phone and "
    "tablet, so keep it platform-neutral and feature-focused — do not name specific "
    "companies, brands, or providers visible on screen (e.g. Google, Apple, Facebook) "
    "since sign-in options and other provider-specific features can differ between "
    "platforms and builds. Respond with ONLY compact JSON on a single line: "
    '{{"title": "...", "subtitle": "..."}} and nothing else — no markdown, no commentary.'
)


class TitleSuggestionError(RuntimeError):
    pass


def suggest_title(image_path: Path, app_name: str, timeout: float = 120) -> dict:
    prompt = PROMPT_TEMPLATE.format(image_path=image_path, app_name=app_name)
    try:
        result = subprocess.run(
            ["claude", "-p", prompt], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise TitleSuggestionError(
            "`claude` CLI not found on PATH — install Claude Code to enable title suggestions."
        ) from exc
    if result.returncode != 0:
        raise TitleSuggestionError(f"claude CLI failed: {result.stderr.strip()}")

    text = result.stdout.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise TitleSuggestionError(f"Could not parse a title suggestion from: {text!r}")
    data = json.loads(text[start : end + 1])
    return {"title": data.get("title", "").strip(), "subtitle": data.get("subtitle", "").strip()}
