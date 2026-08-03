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


TRANSLATE_PROMPT_TEMPLATE = (
    "Translate this App Store screenshot marketing copy for the app '{app_name}' from "
    "{source_lang} into {target_lang}. Keep the same tone (short, punchy titles; "
    "one-sentence subtitles) and roughly the same length — don't add or drop meaning. "
    "Do not name specific companies, brands, or providers (e.g. Google, Apple, Facebook). "
    "Input is a JSON object mapping shot id -> {{\"title\": ..., \"subtitle\": ...}}:\n\n"
    "{titles_json}\n\n"
    "Respond with ONLY the translated JSON object, same keys and shape, on a single line "
    "— no markdown, no commentary."
)


class TitleSuggestionError(RuntimeError):
    pass


def _run_claude(prompt: str, timeout: float) -> dict:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise TitleSuggestionError(
            "`claude` CLI not found on PATH — install Claude Code to enable AI title features."
        ) from exc
    if result.returncode != 0:
        raise TitleSuggestionError(f"claude CLI failed: {result.stderr.strip()}")

    text = result.stdout.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise TitleSuggestionError(f"Could not parse a JSON response from: {text!r}")
    return json.loads(text[start : end + 1])


def suggest_title(image_path: Path, app_name: str, timeout: float = 120) -> dict:
    prompt = PROMPT_TEMPLATE.format(image_path=image_path, app_name=app_name)
    data = _run_claude(prompt, timeout)
    return {"title": data.get("title", "").strip(), "subtitle": data.get("subtitle", "").strip()}


def translate_titles(
    titles: dict, app_name: str, source_lang: str, target_lang: str, timeout: float = 180
) -> dict:
    """Translates a whole {shot_id: {title, subtitle}} mapping into another language."""
    prompt = TRANSLATE_PROMPT_TEMPLATE.format(
        app_name=app_name,
        source_lang=source_lang,
        target_lang=target_lang,
        titles_json=json.dumps(titles, ensure_ascii=False),
    )
    return _run_claude(prompt, timeout)
