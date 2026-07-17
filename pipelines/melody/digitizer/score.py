"""Benchmark scorer: pipeline output vs data/melody/04_verified (plan §6).

Metrics per tune (and aggregated): exact-bar rate (whitespace-insensitive,
beam-agnostic), note-level pitch/rhythm accuracy, an error taxonomy
(±1-step pitch, octave, accidental, duration, tuplet, missing/extra,
structure), and flagged-bar precision/recall against `% flag:` lines in the
hypothesis file. The verified file wins every discrepancy — it is the
owner's ground truth.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path

from .validation import Bar, Note, Tune, effective_pickup, parse_tune

_FLAG_RE = re.compile(r"^%\s*flag:\s*(\d+)\b(.*)$")


def parse_flags(text: str) -> dict[int, str]:
    """`% flag: <bar#> <reason>` lines -> {bar#: reason}."""
    flags: dict[int, str] = {}
    for line in text.splitlines():
        m = _FLAG_RE.match(line.strip())
        if m:
            flags[int(m.group(1))] = m.group(2).strip()
    return flags


def _sig(n: Note) -> tuple:
    """Beam- and whitespace-agnostic note signature."""
    return (n.accidental, n.letter, n.octave, n.units, n.tie)


def _numbered_bars(tune: Tune) -> list[tuple[int, Bar]]:
    """(bar number, bar) with the pickup as bar 0, full bars from 1."""
    pickup = effective_pickup(tune)
    out: list[tuple[int, Bar]] = []
    n = 0
    for sec in tune.sections:
        for bar in sec.bars:
            if bar is pickup:
                out.append((0, bar))
            else:
                n += 1
                out.append((n, bar))
    return out


@dataclass
class TuneScore:
    stem: str
    ref_bars: int = 0
    compared_bars: int = 0
    exact_bars: int = 0
    ref_notes: int = 0
    pitch_correct: int = 0
    rhythm_correct: int = 0
    taxonomy: Counter = field(default_factory=Counter)
    structure_errors: list[str] = field(default_factory=list)
    # flag coverage
    flagged: set[int] = field(default_factory=set)
    wrong_bars: set[int] = field(default_factory=set)

    @property
    def exact_rate(self) -> float:
        return self.exact_bars / self.compared_bars if self.compared_bars else 0.0

    @property
    def pitch_acc(self) -> float:
        return self.pitch_correct / self.ref_notes if self.ref_notes else 0.0

    @property
    def rhythm_acc(self) -> float:
        return self.rhythm_correct / self.ref_notes if self.ref_notes else 0.0

    @property
    def unflagged_wrong(self) -> set[int]:
        return self.wrong_bars - self.flagged

    @property
    def flag_precision(self) -> float:
        return (len(self.flagged & self.wrong_bars) / len(self.flagged)
                if self.flagged else 0.0)

    @property
    def flag_recall(self) -> float:
        return (len(self.flagged & self.wrong_bars) / len(self.wrong_bars)
                if self.wrong_bars else 1.0)


def _classify(ref: Note, hyp: Note) -> str:
    if ref.is_rest != hyp.is_rest:
        return "rest_vs_note"
    if not ref.is_rest:
        dstep = abs(ref.step - hyp.step)
        same_pitch = dstep == 0
        if not same_pitch:
            if ref.letter == hyp.letter and ref.octave != hyp.octave:
                return "octave"
            if dstep == 1:
                return "pitch_step1"
            return "pitch_other"
        if ref.accidental != hyp.accidental:
            return "accidental"
    if ref.units != hyp.units:
        return "duration"
    if ref.tie != hyp.tie:
        return "tie"
    return "other"


def score_pair(hyp_text: str, ref_text: str, stem: str = "") -> TuneScore:
    s = TuneScore(stem=stem)
    ref = parse_tune(ref_text)
    hyp = parse_tune(hyp_text)
    s.flagged = set(parse_flags(hyp_text))

    ref_bars = _numbered_bars(ref)
    hyp_bars = _numbered_bars(hyp)
    s.ref_bars = len(ref_bars)
    s.ref_notes = sum(len(b.notes) for _, b in ref_bars)

    if len(ref_bars) != len(hyp_bars):
        s.structure_errors.append(
            f"{len(hyp_bars)} bars vs {len(ref_bars)} in reference")
        s.taxonomy["structure"] += 1
    n = min(len(ref_bars), len(hyp_bars))
    s.compared_bars = n
    for (rno, rbar), (_, hbar) in zip(ref_bars[:n], hyp_bars[:n]):
        rsig = [_sig(x) for x in rbar.notes]
        hsig = [_sig(x) for x in hbar.notes]
        if rsig == hsig:
            s.exact_bars += 1
            continue
        s.wrong_bars.add(rno)
        # note-level alignment on (letter, octave) to localize errors
        sm = SequenceMatcher(
            a=[(x.letter, x.octave, x.units) for x in rbar.notes],
            b=[(x.letter, x.octave, x.units) for x in hbar.notes],
            autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    rn, hn = rbar.notes[i1 + k], hbar.notes[j1 + k]
                    s.pitch_correct += 1
                    s.rhythm_correct += 1
                    if _sig(rn) != _sig(hn):
                        s.taxonomy[_classify(rn, hn)] += 1
            elif tag == "replace":
                for k in range(max(i2 - i1, j2 - j1)):
                    ri, hj = i1 + k, j1 + k
                    if ri < i2 and hj < j2:
                        rn, hn = rbar.notes[ri], hbar.notes[hj]
                        s.taxonomy[_classify(rn, hn)] += 1
                        if not rn.is_rest and not hn.is_rest \
                                and rn.step == hn.step:
                            s.pitch_correct += 1
                        if rn.units == hn.units:
                            s.rhythm_correct += 1
                    elif ri < i2:
                        s.taxonomy["missing_note"] += 1
                    else:
                        s.taxonomy["extra_note"] += 1
            elif tag == "delete":
                s.taxonomy["missing_note"] += i2 - i1
            elif tag == "insert":
                s.taxonomy["extra_note"] += j2 - j1
    # exact bars where notes matched fully also count toward note accuracy
    for (rno, rbar), (_, hbar) in zip(ref_bars[:n], hyp_bars[:n]):
        if [_sig(x) for x in rbar.notes] == [_sig(x) for x in hbar.notes]:
            s.pitch_correct += len(rbar.notes)
            s.rhythm_correct += len(rbar.notes)
    return s


@dataclass
class Aggregate:
    tunes: list[TuneScore]

    @property
    def exact_rate(self) -> float:
        c = sum(t.compared_bars for t in self.tunes)
        return sum(t.exact_bars for t in self.tunes) / c if c else 0.0

    @property
    def pitch_acc(self) -> float:
        c = sum(t.ref_notes for t in self.tunes)
        return sum(t.pitch_correct for t in self.tunes) / c if c else 0.0

    @property
    def rhythm_acc(self) -> float:
        c = sum(t.ref_notes for t in self.tunes)
        return sum(t.rhythm_correct for t in self.tunes) / c if c else 0.0

    @property
    def taxonomy(self) -> Counter:
        total: Counter = Counter()
        for t in self.tunes:
            total.update(t.taxonomy)
        return total

    @property
    def mean_unflagged_wrong(self) -> float:
        if not self.tunes:
            return 0.0
        return sum(len(t.unflagged_wrong) for t in self.tunes) / len(self.tunes)


def score_dirs(wip_dir: Path, verified_dir: Path) -> Aggregate:
    """Score every wip .abc that has a verified counterpart."""
    scores = []
    for hyp_path in sorted(wip_dir.glob("*.abc")):
        ref_path = verified_dir / hyp_path.name
        if not ref_path.is_file():
            continue
        scores.append(score_pair(
            hyp_path.read_text(encoding="utf-8"),
            ref_path.read_text(encoding="utf-8"),
            stem=hyp_path.stem,
        ))
    return Aggregate(scores)
