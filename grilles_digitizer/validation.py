"""Parse the model's text into JSON and run the per-tune self-check (spec §17)."""

from __future__ import annotations

import json
import re

from .config import SOURCE_CONSTANT
from .manifest import WorkUnit

ALWAYS_PRESENT = (
    "title",
    "title_uncertain",
    "style",
    "form",
    "time_signature",
    "page",
    "source",
    "sections",
)
OPTIONAL_KEYS = ("composer", "year", "tempo", "notation_notes")
VALID_BEAT_KEYS = {"1", "2", "3", "4"}

# Shorthand that must have been expanded away; and non-canonical notations the
# model was told to convert. Presence of any of these in a chord string fails.
_SHORTHAND_CHARS = ("%", "→", "•", "ø", "Δ")
_FORBIDDEN_RE = re.compile(r"7M|/14")


class ValidationError(Exception):
    pass


def parse_json(text: str) -> dict:
    """Parse a bare JSON object, tolerating an accidental ```json fence."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValidationError("output is not a single JSON object")
    return obj


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
    # A bare dash (bar-repeat shorthand) — but '-' is legal inside nothing canonical.
    if chord.strip() == "-":
        raise ValidationError("unexpanded dash in chord")


def validate(obj: dict, unit: WorkUnit) -> dict:
    """Validate against spec §17. Returns the object on success; raises otherwise."""
    for key in ALWAYS_PRESENT:
        if key not in obj:
            raise ValidationError(f"missing required field {key!r}")

    for key in OPTIONAL_KEYS:
        if key in obj and obj[key] in (None, ""):
            raise ValidationError(f"optional field {key!r} present but empty")

    if obj["source"] != SOURCE_CONSTANT:
        raise ValidationError("source constant mismatch")

    if not isinstance(obj["page"], int):
        raise ValidationError("page is not an integer")
    if obj["page"] != unit.page:
        raise ValidationError(f"page {obj['page']} != manifest page {unit.page}")

    if not isinstance(obj["title_uncertain"], bool):
        raise ValidationError("title_uncertain is not a boolean")

    if "fingerprints" in obj:
        raise ValidationError("fingerprints must be absent in production runs")

    sections = obj["sections"]
    if not isinstance(sections, dict):
        raise ValidationError("sections is not an object")
    for sid in sections:
        if "'" in sid or "’" in sid:
            raise ValidationError(f"section key {sid!r} uses a prime")

    notation_notes = obj.get("notation_notes") or {}
    has_no_grid = isinstance(notation_notes, dict) and "no_chord_grid" in notation_notes
    if (sections == {}) != has_no_grid:
        raise ValidationError(
            "sections == {} must hold iff notation_notes.no_chord_grid is present"
        )

    for chord in _iter_chords(sections):
        _check_chord(chord)

    return obj


def needs_review(obj: dict, unit: WorkUnit) -> dict[str, bool]:
    """Which review flags this accepted tune trips (for the run report, spec §4.9)."""
    notation_notes = obj.get("notation_notes") or {}
    has_question = any("?" in chord for chord in _iter_chords(obj.get("sections", {})))
    return {
        "title_uncertain": bool(obj.get("title_uncertain")),
        "no_chord_grid": isinstance(notation_notes, dict)
        and "no_chord_grid" in notation_notes,
        "low_conf_title": unit.low_conf_title,
        "question_chord": has_question,
    }
