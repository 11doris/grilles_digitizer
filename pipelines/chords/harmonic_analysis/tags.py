"""Deterministic `harmonic_fingerprint.tags` (harmonic_analysis_spec §2.4).

The displayer's tag filter menu is built from these strings, so they must
mean the same thing on every tune: each tag is derived from the tune's
structure or from the building blocks / device roles the analyzer marked —
never free LLM prose. Recomputed wherever `harmonic_analysis` is (the
annotate sweep, every `update_annotation` save), so the filter can never
drift from what the overlay actually draws.
"""
from __future__ import annotations

from pipelines.chords.similarity.normalize import sections_view

# Canonical output order: tune-level facts first, then blocks, then devices.
TAG_ORDER = (
    "blues-form", "minor-blues", "minor-key", "verse-present", "modulates",
    "turnaround-ending", "ii-V-chains", "circle-of-fifths",
    "chromatic-descent", "dominant-cycle", "dominant-cycle-bridge",
    "rhythm-changes-bridge", "backdoor-cadence", "iv-minor-cadence",
    "tritone-sub", "passing-diminished",
)

# Blocks whose presence IS the tag. cadence_251 (and its minor twin) would
# tag nearly the whole corpus, so plain cadences stay untagged.
_BLOCK_TAGS = {
    "iiv_chain": "ii-V-chains",
    "circle_of_fifths": "circle-of-fifths",
    "chromatic_descent": "chromatic-descent",
    "rhythm_bridge": "rhythm-changes-bridge",
    "backdoor": "backdoor-cadence",
    "plagal_iv_iv": "iv-minor-cadence",
    "i_i7_iv_ivm_i": "iv-minor-cadence",  # superset outranks plagal_iv_iv
}
_ROLE_TAGS = {"sub_v": "tritone-sub", "dim_passing": "passing-diminished"}
# Named blocks whose chords are a run of dominants in fifths: they outrank
# the code-detected dominant_cycle on the same span, so they carry its tag.
_CYCLE_BLOCKS = {"dominant_cycle", "turnaround_i_vi7_ii7_v7"}


def derive_tags(annotated: dict) -> list[str]:
    """The tune's tags, in TAG_ORDER, from its source fields + analysis."""
    found: set[str] = set()

    minor = (annotated.get("key") or {}).get("mode") == "minor"
    if minor:
        found.add("minor-key")
    if "BLUES" in (annotated.get("form") or "").upper():
        found.add("blues-form")
        if minor:
            found.add("minor-blues")
    if any(s.get("role") == "verse" for s in annotated.get("strains") or []):
        found.add("verse-present")
    if annotated.get("section_keys"):
        found.add("modulates")

    part_bars = sections_view(annotated)
    parts = (annotated.get("harmonic_analysis") or {}).get("parts") or {}
    for pid, part in parts.items():
        bars = part_bars.get(pid) or []
        last_bar = bars[-1].get("bar", len(bars)) if bars else 0
        for block in part.get("blocks") or []:
            # Independent checks: turnaround_i_vi7_ii7_v7 at a part's end
            # is a turnaround-ending AND a dominant cycle.
            if block["id"] in _BLOCK_TAGS:
                found.add(_BLOCK_TAGS[block["id"]])
            if (block["id"].startswith("turnaround")
                    and block["to"][0] >= last_bar - 1):
                found.add("turnaround-ending")
            if block["id"] in _CYCLE_BLOCKS:
                found.add("dominant-cycle-bridge" if pid.startswith("B")
                          else "dominant-cycle")
        for chord in part.get("chords") or []:
            if chord.get("role") in _ROLE_TAGS:
                found.add(_ROLE_TAGS[chord["role"]])
        if any(r.get("kind") == "modulation" for r in part.get("regions") or []):
            found.add("modulates")

    return [t for t in TAG_ORDER if t in found]
