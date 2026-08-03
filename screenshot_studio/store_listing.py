from __future__ import annotations

import json
from pathlib import Path

from . import titles_store
from .ai_titles import TitleSuggestionError, _language_name, run_claude_text
from .config import StudioConfig

# (json key, limit)
_FIELDS = [
    ("play_app_name", 30),
    ("play_short_description", 80),
    ("play_full_description", 4000),
    ("ios_app_name", 30),
    ("ios_subtitle", 30),
    ("ios_promotional_text", 170),
    ("ios_keywords", 100),
    ("ios_description", 4000),
]

PROMPT_TEMPLATE = (
    "Write Google Play and App Store listing copy for the Flutter app '{app_name}', "
    "in {language}. Base it on these captured feature screenshots (shot id -> title/"
    "subtitle already used on the store screenshots themselves):\n\n{shots_json}\n\n"
    "Do not name specific companies, brands, or providers visible on screen (e.g. "
    "Google, Apple, Facebook) since those can differ between platforms and builds.\n\n"
    "Respond with ONLY compact JSON on a single line, no markdown, no commentary, "
    "with exactly these keys and character limits (stay under every limit — this is "
    "hard-checked by code, not judged by you):\n"
    '{{"play_app_name": "...", // <=30 chars\n'
    '"play_short_description": "...", // <=80 chars\n'
    '"play_full_description": "...", // <=4000 chars\n'
    '"ios_app_name": "...", // <=30 chars\n'
    '"ios_subtitle": "...", // <=30 chars\n'
    '"ios_promotional_text": "...", // <=170 chars\n'
    '"ios_keywords": "...", // <=100 chars, comma-separated, no spaces after commas\n'
    '"ios_description": "..."}} // <=4000 chars\n'
    "play_app_name and ios_app_name may repeat the same text. play_full_description "
    "and ios_description may repeat the same text."
)

_TEMPLATE = """\
# {app_name} — Store Listing Copy

Draft copy for the Google Play Console and App Store Connect listing forms.
Character counts are shown against each field's limit — edit freely, just keep an eye on the limits if you change the wording.

## Google Play

**App name** — {play_app_name[count]} / {play_app_name[limit]}
```
{play_app_name[text]}
```

**Short description** — {play_short_description[count]} / {play_short_description[limit]}
```
{play_short_description[text]}
```

**Full description** — {play_full_description[count]} / {play_full_description[limit]}
```
{play_full_description[text]}
```

## App Store (iOS)

**App name** — {ios_app_name[count]} / {ios_app_name[limit]}
```
{ios_app_name[text]}
```

**Subtitle** — {ios_subtitle[count]} / {ios_subtitle[limit]}
```
{ios_subtitle[text]}
```

**Promotional text** — {ios_promotional_text[count]} / {ios_promotional_text[limit]}
```
{ios_promotional_text[text]}
```

**Keywords** — {ios_keywords[count]} / {ios_keywords[limit]} (comma-separated, no spaces after commas — spaces count toward the limit)
```
{ios_keywords[text]}
```

**Description** — {ios_description[count]} / {ios_description[limit]}
```
{ios_description[text]}
```

---

Notes:
- Neither store's copy names a specific sign-in provider, consistent with the screenshot subtitles.
- Google Play's full description supports basic text only (no markdown); paste the text between the code fences as-is.
- App Store keywords are not shown to users — they're only used for search indexing.
"""


def _parse_response(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise TitleSuggestionError(f"Could not parse a JSON response from: {raw!r}")
    data = json.loads(raw[start : end + 1])
    missing = [key for key, _ in _FIELDS if key not in data]
    if missing:
        raise TitleSuggestionError(f"Response missing keys {missing}: {raw!r}")
    return data


_MAX_ATTEMPTS = 4


def generate_store_listing(cfg: StudioConfig, lang: str, timeout: float = 180) -> Path:
    shots = titles_store.load_titles(cfg, lang)
    if not shots:
        raise TitleSuggestionError(
            f"No shot titles found for '{lang}' at {cfg.titles_path(lang)} — capture and "
            "title some shots first."
        )

    prompt = PROMPT_TEMPLATE.format(
        app_name=cfg.app.name,
        language=_language_name(lang),
        shots_json=json.dumps(shots, ensure_ascii=False, indent=2),
    )

    fields: dict | None = None
    over_limit: list[str] = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        data = _parse_response(run_claude_text(prompt, timeout))

        # Character counts are computed here in Python, never trusted from the model —
        # some languages (e.g. Greek) run longer per word than the model expects, so it
        # reliably overshoots tight limits like the 30-char iOS subtitle.
        fields = {
            key: {"text": data[key], "count": len(data[key]), "limit": limit}
            for key, limit in _FIELDS
        }
        over_limit = [key for key, f in fields.items() if f["count"] > f["limit"]]
        if not over_limit:
            break

        overage = "; ".join(
            f"{key}: {fields[key]['count']} chars, limit is {fields[key]['limit']} — "
            f"shorten by at least {fields[key]['count'] - fields[key]['limit']}"
            for key in over_limit
        )
        prompt = (
            f"Your previous JSON response exceeded the character limit on these fields: "
            f"{overage}. Rewrite ONLY those field(s), keeping the rest identical, and "
            f"respond again with the full JSON object in the same shape as before, still "
            f"ONLY compact JSON on a single line, no markdown, no commentary. Previous "
            f"response was: {json.dumps(data, ensure_ascii=False)}"
        )
    else:
        raise TitleSuggestionError(
            f"Could not get response under character limits after {_MAX_ATTEMPTS} attempts, "
            "still over on: "
            + ", ".join(f"{key} ({fields[key]['count']}/{fields[key]['limit']})" for key in over_limit)
        )

    markdown = _TEMPLATE.format(app_name=cfg.app.name, **fields)

    dest = cfg.store_listing_path(lang)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(markdown)
    return dest
