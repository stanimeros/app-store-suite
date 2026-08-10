from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from . import store_listing, titles_store
from .config import StudioConfig

# Above this ratio (0-1, via difflib's SequenceMatcher — good enough for catching
# copy-pasted/near-identical marketing lines without pulling in a real NLP dep),
# two different copy fields are flagged as likely saying the same thing twice.
_SIMILARITY_THRESHOLD = 0.7

# Field pairs allowed to legitimately repeat the same text — the app name and the
# full description are meant to match between Play and iOS (see PROMPT_TEMPLATE
# below), so flagging them would just be noise on every listing.
_ALLOWED_DUPLICATE_PAIRS = {
    frozenset({"play_app_name", "ios_app_name"}),
    frozenset({"play_full_description", "ios_description"}),
}


@dataclass
class Issue:
    lang: str
    kind: str  # "limit" | "duplicate"
    detail: str

    def __str__(self) -> str:
        return f"[{self.lang}] {self.detail}"


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def validate_lang(cfg: StudioConfig, lang: str) -> list[Issue]:
    """Checks one language's shot titles/subtitles + store listing text for
    over-limit fields and near-duplicate copy across fields (e.g. a screenshot
    title that just repeats the store subtitle wastes two separate marketing
    slots on the same line)."""
    issues: list[Issue] = []

    listing = store_listing.read_listing(cfg, lang)
    for key, limit, _, _ in store_listing._FIELDS:
        text = listing.get(key, "")
        if text and len(text) > limit:
            issues.append(
                Issue(lang, "limit", f"{key} is {len(text)} chars, limit is {limit}")
            )

    titles = titles_store.load_titles(cfg, lang)

    # Every piece of marketing copy for this language, labeled by where it shows up,
    # so a similarity hit can name both sides plainly.
    snippets: list[tuple[str, str]] = []
    for shot_id, shot in titles.items():
        if shot.get("title"):
            snippets.append((f"shot '{shot_id}' title", shot["title"]))
        if shot.get("subtitle"):
            snippets.append((f"shot '{shot_id}' subtitle", shot["subtitle"]))
    for key, text in listing.items():
        if text and key != "ios_keywords":  # keywords are a CSV list, not prose — comparing it as a sentence is noise
            snippets.append((key, text))

    for i, (label_a, text_a) in enumerate(snippets):
        for label_b, text_b in snippets[i + 1 :]:
            if frozenset({label_a, label_b}) in _ALLOWED_DUPLICATE_PAIRS:
                continue
            ratio = _similarity(text_a, text_b)
            if ratio >= _SIMILARITY_THRESHOLD:
                issues.append(
                    Issue(
                        lang,
                        "duplicate",
                        f"{label_a} and {label_b} are {ratio:.0%} similar "
                        f"({text_a!r} vs {text_b!r})",
                    )
                )

    return issues


def validate_all(cfg: StudioConfig, langs: list[str] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for lang in langs or cfg.languages:
        issues.extend(validate_lang(cfg, lang))
    return issues
