"""Deterministic functional harmony analysis (docs/specs/harmonic_analysis_spec.md).

Pure functions of (chords, key, section_keys) — no LLM, no I/O. The
`harmonic_analysis` field written into 05_annotated is recomputed on every
key correction, so it can never go stale.
"""
from .analyze import ANALYSIS_VERSION, analyze_annotated, analyze_tune
from .tags import derive_tags

__all__ = ["ANALYSIS_VERSION", "analyze_annotated", "analyze_tune",
           "derive_tags"]
