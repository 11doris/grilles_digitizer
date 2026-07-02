"""Parse the model's text into JSON and run the per-tune self-check (spec §17).

`title`, `page`, and `source` are injected by the runner before validation (spec §5),
so they are always present here. Validation has two tiers:

* `validate()` raises `ValidationError` on **structural** problems (checks 1, 4-13) —
  the runner retries those.
* `review_flags()` reports non-fatal conditions a human should look at (spec §4.9):
  a missing always-present field (§17 check 2) and a `no_chord_grid` note. A tune with
  a missing required field is still accepted and written, just flagged.
"""

from __future__ import annotations

import json
import re

from .config import SOURCE_CONSTANT
from .manifest import WorkUnit

ALWAYS_PRESENT = (
    "title",
    "style",
    "form",
    "time_signature",
    "page",
    "source",
    "sections",
)
OPTIONAL_KEYS = (
    "composer",
    "year",
    "tempo",
    "recordings",
    "variants",
    "same_chord_changes",
    "notation_notes",
)
VALID_BEAT_KEYS = {"1", "2", "3", "4"}

# Shorthand that must have been expanded away; and non-canonical notations the
# model was told to convert. Presence of any of these in a chord string fails.
_SHORTHAND_CHARS = ("%", "→", "•", "ø", "Δ")
_FORBIDDEN_RE = re.compile(r"7M|/14")

# A multi-strain section key: <prefix>_<section-id> (spec §8.5). The prefix is a
# numbered strain (s1, s2, …) or a lowercase named strain (trio, clarinet). `sid` is
# matched as `.*` so an empty suffix (e.g. "s1_") still matches and is rejected below.
_STRAIN_KEY_RE = re.compile(r"^(?P<prefix>s\d+|[a-z][a-z0-9]*)_(?P<sid>.*)$")
_NUMBERED_STRAIN_RE = re.compile(r"^s(\d+)$")


class ValidationError(Exception):
    pass


def parse_json(text: str) -> dict:
    """Parse a bare JSON object, tolerating a ```json fence or a prose preamble.

    The prompt and the assistant prefill ask for a bare object, but as a safety net
    we also recover the first JSON object embedded in surrounding prose.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        obj = _extract_object(stripped)
    if not isinstance(obj, dict):
        raise ValidationError("output is not a single JSON object")
    return obj


def _extract_object(text: str) -> dict:
    """Recover the first complete JSON object starting at the first '{'."""
    start = text.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            pass
    raise ValidationError(
        f"not valid JSON: no parseable JSON object found (starts with {text[:30]!r})"
    )


def _iter_chords(sections: dict):
    for bars in sections.values():
        if not isinstance(bars, list):
            raise ValidationError("a section is not a list of bars")
        for bar in bars:
            if not isinstance(bar, dict) or "bar" not in bar or "beats" not in bar:
                raise ValidationError("a bar is missing 'bar' or 'beats'")
            beats = bar["beats"]
            if not isinstance(beats, dict) or not beats:
                raise ValidationError("a bar has no beats map")
            for key, chord in beats.items():
                if key not in VALID_BEAT_KEYS:
                    raise ValidationError(f"bad beat key {key!r}")
                if not isinstance(chord, str):
                    raise ValidationError("a beat value is not a string")
                yield chord


def _check_chord(chord: str) -> None:
    for ch in _SHORTHAND_CHARS:
        if ch in chord:
            raise ValidationError(f"unexpanded/non-canonical mark in chord {chord!r}")
    if _FORBIDDEN_RE.search(chord):
        raise ValidationError(f"non-canonical notation in chord {chord!r}")
    if chord.strip() == "-":
        raise ValidationError("unexpanded dash in chord")


def _check_section_keys(sections: dict) -> None:
    """No primes, and well-formed multi-strain keys (spec §8.5 / §17 check 13)."""
    numbered = set()
    for key in sections:
        if "'" in key or "’" in key:
            raise ValidationError(f"section key {key!r} uses a prime")
        match = _STRAIN_KEY_RE.match(key)
        if not match:
            continue  # plain key (A, B1, intro, …) — fine
        if not match.group("sid"):
            raise ValidationError(f"strain key {key!r} has an empty section id")
        num = _NUMBERED_STRAIN_RE.match(match.group("prefix"))
        if num:
            numbered.add(int(num.group(1)))
    if numbered and sorted(numbered) != list(range(1, max(numbered) + 1)):
        raise ValidationError(
            f"numbered strains must run s1..sN contiguously; got {sorted(numbered)}"
        )


def validate(obj: dict, unit: WorkUnit) -> dict:
    """Structural self-check (spec §17). Returns the object; raises on failure.

    Does NOT raise on a missing always-present field — that is an accept-and-flag
    condition handled by `review_flags()` (spec §17 check 2 / §4.9).
    """
    for key in OPTIONAL_KEYS:
        if key in obj and obj[key] in (None, ""):
            raise ValidationError(f"optional field {key!r} present but empty")

    if "source" in obj and obj["source"] != SOURCE_CONSTANT:
        raise ValidationError("source constant mismatch")

    if "page" in obj:
        if not isinstance(obj["page"], int):
            raise ValidationError("page is not an integer")
        if obj["page"] != unit.page:
            raise ValidationError(f"page {obj['page']} != manifest page {unit.page}")

    if "fingerprints" in obj:
        raise ValidationError("fingerprints must be absent in production runs")

    sections = obj.get("sections")
    if sections is not None:
        if not isinstance(sections, dict):
            raise ValidationError("sections is not an object")
        _check_section_keys(sections)

        notation_notes = obj.get("notation_notes") or {}
        has_no_grid = (
            isinstance(notation_notes, dict) and "no_chord_grid" in notation_notes
        )
        if (sections == {}) != has_no_grid:
            raise ValidationError(
                "sections == {} must hold iff notation_notes.no_chord_grid is present"
            )

        for chord in _iter_chords(sections):
            _check_chord(chord)

    return obj


def review_flags(obj: dict, unit: WorkUnit) -> dict[str, bool]:
    """Non-fatal conditions for the run report (spec §4.9)."""
    notation_notes = obj.get("notation_notes") or {}
    return {
        "missing_required_field": any(k not in obj for k in ALWAYS_PRESENT),
        "no_chord_grid": isinstance(notation_notes, dict)
        and "no_chord_grid" in notation_notes,
    }
