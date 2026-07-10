"""Voter 1 — deterministic functional key scorer (tune_similarity_spec §3.2).

Pure Python, no external services, free to rerun. For each of the 24
candidate keys it accumulates the fixed feature list from the spec:

  * ii-V-I resolutions into the candidate tonic (major and minor shapes)
  * V -> I cadences (without the ii)
  * duration on a mode-compatible tonic chord (fraction of half-bar slots)
  * final-bar bonus (final chord of the *flattened* form, so `A A B A'`
    endings are used) and a smaller first-bar bonus
  * mode match of the tonic chord's quality class (folded into the
    per-quality tonic weights)

Exact weights are implementation-tunable (spec); the tests in
tests/test_scorer.py pin the behaviour on the corpus' hard cases
(turnaround endings, Picardy third, Con Alma).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pipelines.chords.similarity.normalize import (
    PC_NAME, Chord, Slot, expand_tune, flatten, pitch_class,
)

# --- tunable weights -------------------------------------------------------

W_251 = 4.0          # per ii-V-I resolution into the tonic
W_V1 = 1.5           # per V -> I cadence (the ii-less shape)
W_DURATION = 4.0     # x fraction of slots spent on a compatible tonic chord
W_FINAL = 1.8        # final sounding chord is a compatible tonic
W_FIRST = 0.8        # tonic (compatible quality) in the first bar

# Dominant resolutions into a *minor* target are damped: V/ii -> ii and
# V/vi -> vi are everyday major-key moves (Au Privave's D7 -> Gm7), so a bare
# dominant landing on a minor chord is weak evidence for a minor key.
MINOR_RESOLUTION_DAMP = 0.6

# How strongly a chord of a given quality class on the tonic root "is" the
# tonic, per mode. Dominant quality on the major tonic gets half credit so
# blues heads (F7 as I) still vote for their key without letting every V7
# claim tonic-hood; a major chord on the minor tonic is the Picardy case.
_TONIC_QUALITY = {
    "major": {"maj": 1.0, "dom": 0.5, "sus": 0.3},
    "minor": {"min": 1.0, "maj": 0.3},
}

TUNE_MARGIN_THRESHOLD = 0.15    # scorer counts as "confident" at/above this

# Per-section pass: 8 bars are noisy, so bias strongly toward "no modulation"
# (spec §3.2/§10). A local key is recorded only when it beats the global key
# decisively AND shows a real cadential arrival + tonic residency of its own.
SECTION_MARGIN_THRESHOLD = 0.5
SECTION_MIN_SLOTS = 8            # at least 4 bars
SECTION_MIN_TONIC_FRACTION = 0.20


@dataclass
class KeyVote:
    tonic: str
    mode: str
    margin: float
    section_keys: dict[str, dict] = field(default_factory=dict)

    def to_json(self) -> dict:
        out = {"tonic": self.tonic, "mode": self.mode, "margin": round(self.margin, 4)}
        if self.section_keys:
            out["section_keys"] = self.section_keys
        return out


def _compressed(chords: list[Chord]) -> list[Chord]:
    """Successive distinct sounding (root, quality) events; N.C. dropped."""
    out: list[Chord] = []
    for ch in chords:
        if not ch.is_sounding:
            continue
        if not out or (ch.root_pc, ch.quality) != (out[-1].root_pc, out[-1].quality):
            out.append(ch)
    return out


def _tonic_factor(ch: Chord, tonic_pc: int, mode: str) -> float:
    if ch.is_sounding and ch.root_pc == tonic_pc:
        return _TONIC_QUALITY[mode].get(ch.quality, 0.0)
    return 0.0


def _resolution_damp(mode: str) -> float:
    return MINOR_RESOLUTION_DAMP if mode == "minor" else 1.0


def _cadences(slots: list[Slot], tonic_pc: int, mode: str
              ) -> tuple[float, float, int]:
    """(ii-V-I score, V-I score, count of full-strength arrivals)."""
    changes = _compressed([s.chord for s in slots])
    ii_pc, v_pc = (tonic_pc + 2) % 12, (tonic_pc + 7) % 12
    damp = _resolution_damp(mode)
    s251 = sv1 = 0.0
    full_arrivals = 0

    for a, b in zip(changes, changes[1:]):
        if a.root_pc == v_pc and a.quality == "dom":
            f = _tonic_factor(b, tonic_pc, mode)
            sv1 += W_V1 * f * damp
            if f >= 1.0:
                full_arrivals += 1

    for a, b, c in zip(changes, changes[1:], changes[2:]):
        if not (b.root_pc == v_pc and b.quality == "dom") or a.root_pc != ii_pc:
            continue
        resolved = _tonic_factor(c, tonic_pc, mode)
        if resolved <= 0:
            continue
        # canonical ii quality per mode gets full weight, the other shape less
        if mode == "major" and a.quality in ("min", "m7b5"):
            s251 += W_251 * resolved * (1.0 if a.quality == "min" else 0.6)
        elif mode == "minor" and a.quality in ("m7b5", "min"):
            s251 += W_251 * resolved * (1.0 if a.quality == "m7b5" else 0.6)

    return s251, sv1, full_arrivals


def _tonic_duration(slots: list[Slot], tonic_pc: int, mode: str,
                    full_quality_only: bool = False) -> float:
    """Fraction of sounding slots spent on a compatible tonic chord."""
    sounding = [s for s in slots if s.chord.is_sounding]
    if not sounding:
        return 0.0
    total = 0.0
    for s in sounding:
        f = _tonic_factor(s.chord, tonic_pc, mode)
        total += (1.0 if f >= 1.0 else 0.0) if full_quality_only else f
    return total / len(sounding)


def _score_key(slots: list[Slot], tonic_pc: int, mode: str) -> float:
    s251, sv1, _ = _cadences(slots, tonic_pc, mode)
    score = s251 + sv1
    score += W_DURATION * _tonic_duration(slots, tonic_pc, mode)

    sounding = [s for s in slots if s.chord.is_sounding]
    if sounding:
        score += W_FINAL * _tonic_factor(sounding[-1].chord, tonic_pc, mode)
    first_bar = [s for s in slots
                 if (s.section, s.bar) == (slots[0].section, slots[0].bar)]
    score += W_FIRST * max(
        (_tonic_factor(s.chord, tonic_pc, mode) for s in first_bar), default=0.0)
    return score


def _best_two(slots: list[Slot]) -> tuple[tuple[int, str, float], float]:
    scored = sorted(
        ((pc, mode, _score_key(slots, pc, mode))
         for pc in range(12) for mode in ("major", "minor")),
        key=lambda t: t[2], reverse=True)
    best, runner = scored[0], scored[1]
    margin = (best[2] - runner[2]) / best[2] if best[2] > 0 else 0.0
    return best, margin


def _section_local_key(slots: list[Slot], global_pc: int, global_mode: str
                       ) -> dict | None:
    """Local key for one section, or None (the strongly preferred answer)."""
    if len(slots) < SECTION_MIN_SLOTS:
        return None
    (pc, mode, best), _ = _best_two(slots)
    if (pc, mode) == (global_pc, global_mode) or best <= 0:
        return None
    global_here = _score_key(slots, global_pc, global_mode)
    local_margin = (best - global_here) / best
    if local_margin < SECTION_MARGIN_THRESHOLD:
        return None
    # Decisive on the numbers is not enough: the section must actually
    # *arrive* on and *live* on the local tonic, otherwise a dominant-cycle
    # bridge would read as a modulation to wherever its last V7 points.
    _, _, full_arrivals = _cadences(slots, pc, mode)
    if full_arrivals < 1:
        return None
    if _tonic_duration(slots, pc, mode, full_quality_only=True) < SECTION_MIN_TONIC_FRACTION:
        return None
    return {"tonic": PC_NAME[pc], "mode": mode, "margin": round(local_margin, 4)}


def _local_keys(sections: dict[str, list[Slot]], tonic_pc: int, mode: str
                ) -> dict[str, dict]:
    section_keys: dict[str, dict] = {}
    for name, slots in sections.items():
        local = _section_local_key(slots, tonic_pc, mode)
        if local is not None:
            section_keys[name] = local
    return section_keys


def section_local_keys(tune: dict, tonic: str, mode: str) -> dict[str, dict]:
    """The per-section pass alone, under a given (e.g. human-corrected)
    global key — used by the §3.5 update routine to re-detect local keys
    after a key correction."""
    return _local_keys(expand_tune(tune), pitch_class(tonic), mode)


def score_tune(tune: dict) -> KeyVote:
    """Score the whole tune, then rerun per section for local keys."""
    sections = expand_tune(tune)
    full = flatten(sections)
    (tonic_pc, mode, _), margin = _best_two(full)

    return KeyVote(PC_NAME[tonic_pc], mode, margin,
                   _local_keys(sections, tonic_pc, mode))
