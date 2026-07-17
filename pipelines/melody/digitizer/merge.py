"""Merge two decorrelated reads into one ABC, flagging disagreements (plan §4).

Pass A (full page) and Pass B (ruler strips) are aligned bar-by-bar against
the skeleton section plan. A bar where the two agree (pitch + rhythm, ignoring
beaming/spacing) is accepted with confidence. A bar where they disagree — or
where either read is malformed — is a FLAGGED bar: the merge picks the better
candidate (the one that sums to the meter; ties broken toward Pass B, which
has the pitch ruler) and records both readings for the repair pass.

The point of two decorrelated reads: ±1-step pitch errors are correlated
between two looks at the SAME pixels, so a second read of the same full page
would not catch them — but the ruler strips are different evidence, so an
error that survives both is rarer and, when they differ, we KNOW to look.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .score import _sig
from .skeleton import Skeleton
from .validation import Bar, Tune, effective_pickup, parse_tune


@dataclass
class MergedBar:
    global_no: int  # pickup = 0, full bars from 1
    section_idx: int
    label: str
    chosen_src: str
    read_a_src: str
    read_b_src: str
    agree: bool
    valid: bool  # chosen bar sums to meter (pickup exempt)
    flagged: bool
    reason: str = ""


@dataclass
class MergeResult:
    bars: list[MergedBar]
    pickup_src: str | None
    flagged: list[int] = field(default_factory=list)
    agreement: float = 0.0
    note: str = ""


def _sections_with_bars(tune: Tune) -> tuple[Bar | None, list[tuple[str, list[Bar]]]]:
    pickup = effective_pickup(tune)
    out = []
    for sec in tune.body_sections:
        bars = [b for b in sec.bars if b is not pickup]
        out.append((sec.label or "", bars))
    return pickup, out


def _bar_sig(bar: Bar) -> list[tuple]:
    return [_sig(n) for n in bar.notes]


def merge_reads(skeleton: Skeleton, abc_a: str, abc_b: str) -> MergeResult:
    meter = skeleton.meter_units
    try:
        tune_a = parse_tune(abc_a)
    except Exception:
        tune_a = None
    try:
        tune_b = parse_tune(abc_b)
    except Exception:
        tune_b = None

    if tune_a is None and tune_b is None:
        return MergeResult(bars=[], pickup_src=None, note="both reads unparseable")
    if tune_a is None:
        return _single(skeleton, tune_b, "Pass A unparseable; using Pass B")
    if tune_b is None:
        return _single(skeleton, tune_a, "Pass B unparseable; using Pass A")

    pickup_a, secs_a = _sections_with_bars(tune_a)
    pickup_b, secs_b = _sections_with_bars(tune_b)
    pickup = pickup_a or pickup_b
    pickup_src = pickup.src if pickup is not None else None

    bars: list[MergedBar] = []
    flagged: list[int] = []
    agree_count = 0
    global_no = 0
    n_sections = len(skeleton.sections)
    for si in range(n_sections):
        label = skeleton.sections[si].label
        exp = skeleton.sections[si].bars
        a_bars = secs_a[si][1] if si < len(secs_a) else []
        b_bars = secs_b[si][1] if si < len(secs_b) else []
        count = max(exp, len(a_bars), len(b_bars))
        for bi in range(count):
            global_no += 1
            a = a_bars[bi] if bi < len(a_bars) else None
            b = b_bars[bi] if bi < len(b_bars) else None
            mb = _merge_one(global_no, si, label, a, b, meter)
            bars.append(mb)
            if mb.agree:
                agree_count += 1
            if mb.flagged:
                flagged.append(global_no)
    total = len(bars)
    return MergeResult(
        bars=bars, pickup_src=pickup_src, flagged=flagged,
        agreement=agree_count / total if total else 0.0)


def _merge_one(no: int, si: int, label: str, a: Bar | None, b: Bar | None,
               meter) -> MergedBar:
    a_src = a.src if a is not None else ""
    b_src = b.src if b is not None else ""
    a_ok = a is not None and a.units == meter
    b_ok = b is not None and b.units == meter

    if a is not None and b is not None and _bar_sig(a) == _bar_sig(b):
        return MergedBar(no, si, label, a_src, a_src, b_src, agree=True,
                         valid=a_ok, flagged=not a_ok,
                         reason="" if a_ok else "agreed but wrong bar sum")

    # disagreement (or a missing read): choose the better candidate
    if a is None:
        chosen, valid, reason = b_src, b_ok, "only Pass B read this bar"
    elif b is None:
        chosen, valid, reason = a_src, a_ok, "only Pass A read this bar"
    elif a_ok and not b_ok:
        chosen, valid, reason = a_src, True, "reads differ; Pass B bar-sum bad"
    elif b_ok and not a_ok:
        chosen, valid, reason = b_src, True, "reads differ; Pass A bar-sum bad"
    else:
        # both valid (or both bad): prefer Pass A. (The plan expected Pass B's
        # ruler strips to win on pitch, but benchmarking showed the fragmented
        # ruler strips read WORSE than the full page — see the Phase-3 note.)
        chosen, valid = a_src, a_ok
        reason = "reads differ" + ("" if a_ok else "; both bar-sums bad")
    return MergedBar(no, si, label, chosen, a_src, b_src, agree=False,
                     valid=valid, flagged=True, reason=reason)


def _single(skeleton: Skeleton, tune: Tune, note: str) -> MergeResult:
    """One usable read: accept its bars, flag any that don't sum."""
    meter = skeleton.meter_units
    pickup, secs = _sections_with_bars(tune)
    bars: list[MergedBar] = []
    flagged: list[int] = []
    global_no = 0
    for si in range(len(skeleton.sections)):
        label = skeleton.sections[si].label
        sec_bars = secs[si][1] if si < len(secs) else []
        for bi in range(max(skeleton.sections[si].bars, len(sec_bars))):
            global_no += 1
            b = sec_bars[bi] if bi < len(sec_bars) else None
            src = b.src if b is not None else ""
            ok = b is not None and b.units == meter
            mb = MergedBar(global_no, si, label, src, src, "", agree=False,
                           valid=ok, flagged=not ok,
                           reason="" if ok else "bar sum bad / missing")
            bars.append(mb)
            if mb.flagged:
                flagged.append(global_no)
    return MergeResult(bars=bars, pickup_src=pickup.src if pickup else None,
                       flagged=flagged, agreement=0.0, note=note)


def assemble_body(skeleton: Skeleton, result: MergeResult) -> str:
    """Lay out the merged bars as an ABC body: pickup, then each section with
    its "^label" and 4 bars per line, `||` at section ends, `|]` at the end."""
    lines: list[str] = []
    by_section: dict[int, list[MergedBar]] = {}
    for mb in result.bars:
        by_section.setdefault(mb.section_idx, []).append(mb)

    n_sections = len(skeleton.sections)
    prefix = ""
    if result.pickup_src:
        prefix = f"{result.pickup_src} || "
    for si in range(n_sections):
        label = skeleton.sections[si].label
        section_bars = [mb.chosen_src or "x8" for mb in by_section.get(si, [])]
        terminal = "|]" if si == n_sections - 1 else "||"
        head = f'{prefix}"^{label}" '
        prefix = ""
        # 4 bars per source line
        for row_start in range(0, len(section_bars), 4):
            row = section_bars[row_start:row_start + 4]
            is_last_row = row_start + 4 >= len(section_bars)
            sep = " | ".join(row)
            end = f" {terminal}" if is_last_row else " |"
            if row_start == 0:
                lines.append(f"{head}{sep}{end}")
            else:
                lines.append(f"{sep}{end}")
    return "\n".join(lines)
