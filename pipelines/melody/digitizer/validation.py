"""Parse and validate the house ABC dialect (plan §5).

The dialect is exactly what `data/melody/04_verified` uses: notes
`[_=^]*[A-Ga-g][,']*<len><'-'?>`, rests `z`/`x`, `(3` triplets (eighth
`(3xyz` and quarter `(3X2Y2Z2`), barlines `|` `||` `|]`, section labels as
`"^A"` annotations, adjacency = beaming, 4 bars per source line.

Two tiers:

* hard errors — unparseable tokens, bar-sum mismatches, section/bar-count
  clashes with the skeleton plan, out-of-range pitches, broken ties. The
  runner retries these once, then flags.
* soft warnings — v2-style flat spacing (no beam groups at all), leaps
  larger than an octave. Recorded, never blocking.

`parse_tune()` is also the loader for the benchmark scorer (score.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

_HEADER_RE = re.compile(r"^[A-Za-z]:")
_TOKEN_RE = re.compile(
    r"""(?P<ws>\s+)
      | (?P<label>"\^(?P<label_text>[^"]*)")
      | (?P<tuplet>\(3)
      | (?P<slur_open>\()
      | (?P<slur_close>\))
      | (?P<bar>\|\]|\|\||\|:|:\||\|)
      | (?P<note>(?P<acc>[_=^]{1,2})?(?P<letter>[A-Ga-gzx])(?P<marks>[,']*)
          (?P<len>\d+)?(?P<frac>/\d*)?(?P<tie>-)?)
      | (?P<loosetie>-)
      | (?P<other>\S)
    """,
    re.VERBOSE,
)

_LETTERS = "CDEFGAB"


@dataclass
class Note:
    letter: str  # 'A'..'G' upper, or 'z'/'x' for rests
    accidental: str  # '' '_' '__' '=' '^' '^^' — as written
    octave: int  # scientific octave: plain uppercase = 4, lowercase = 5
    units: Fraction  # duration in L: units after tuplet scaling
    tie: bool
    beamed_prev: bool  # no whitespace between this token and the previous note
    src: str

    @property
    def is_rest(self) -> bool:
        return self.letter in "zx"

    @property
    def step(self) -> int:
        """Diatonic index (C0=0, +1 per scale step); rests raise ValueError."""
        if self.is_rest:
            raise ValueError("rest has no pitch")
        return self.octave * 7 + _LETTERS.index(self.letter)


@dataclass
class Bar:
    notes: list[Note] = field(default_factory=list)
    src: str = ""
    terminal: str = "|"

    @property
    def units(self) -> Fraction:
        return sum((n.units for n in self.notes), Fraction(0))


@dataclass
class Section:
    label: str | None  # None = pickup prelude before the first label
    bars: list[Bar] = field(default_factory=list)


@dataclass
class Tune:
    header_lines: list[str]
    headers: dict[str, str]  # first value per header letter
    meter_units: Fraction
    unit_len: str
    key: str
    sections: list[Section]

    @property
    def prelude(self) -> Bar | None:
        """First bar of a leading anonymous (label-less) section, if any."""
        if self.sections and self.sections[0].label is None:
            bars = self.sections[0].bars
            return bars[0] if bars else None
        return None

    @property
    def body_sections(self) -> list[Section]:
        return [s for s in self.sections if s.label is not None]

    @property
    def all_bars(self) -> list[Bar]:
        return [b for s in self.sections for b in s.bars]


@dataclass
class Finding:
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.where}: {self.message}"


@dataclass
class Report:
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ParseError(Exception):
    pass


def _meter_units(meter: str, unit_len: str) -> Fraction:
    num, den = meter.split("/")
    lnum, lden = unit_len.split("/")
    return Fraction(int(num), int(den)) / Fraction(int(lnum), int(lden))


def parse_tune(text: str) -> Tune:
    """Parse a full ABC file in the house dialect. Raises ParseError."""
    header_lines: list[str] = []
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _HEADER_RE.match(line) and not body_lines:
            header_lines.append(line)
            headers.setdefault(line[0], line[2:])
        else:
            body_lines.append(line)
    if "M" not in headers or "K" not in headers:
        raise ParseError("missing M: or K: header")
    unit_len = headers.get("L", "1/8")
    meter_units = _meter_units(headers["M"], unit_len)

    sections: list[Section] = [Section(label=None)]
    bar = Bar()
    tuplet_left = 0
    slur_depth = 0
    prev_was_note_no_ws = False  # last token was a note with no ws after it

    def close_bar(terminal: str) -> None:
        nonlocal bar
        if bar.notes:
            bar.terminal = terminal
            bar.src = bar.src.strip()
            sections[-1].bars.append(bar)
            bar = Bar()

    for line_no, line in enumerate(body_lines, 1):
        pos = 0
        while pos < len(line):
            m = _TOKEN_RE.match(line, pos)
            if m is None:  # cannot happen: 'other' matches any non-space
                raise ParseError(f"body line {line_no}: unlexable at {pos}")
            pos = m.end()
            if m.group("ws"):
                prev_was_note_no_ws = False
                bar.src += " "
                continue
            if m.group("other"):
                raise ParseError(
                    f"body line {line_no}: unknown token {m.group('other')!r}")
            if m.group("label"):
                close_bar("|")
                label = m.group("label_text")
                if sections[-1].label is None and not sections[-1].bars:
                    sections[-1] = Section(label=label)
                else:
                    sections.append(Section(label=label))
                prev_was_note_no_ws = False
                continue
            if m.group("bar"):
                close_bar(m.group("bar"))
                prev_was_note_no_ws = False
                continue
            if m.group("tuplet"):
                if tuplet_left:
                    raise ParseError(f"body line {line_no}: nested (3")
                tuplet_left = 3
                bar.src += "(3"
                prev_was_note_no_ws = False
                continue
            if m.group("slur_open"):
                slur_depth += 1
                bar.src += "("
                continue
            if m.group("slur_close"):
                if slur_depth == 0:
                    raise ParseError(f"body line {line_no}: ')' without '('")
                slur_depth -= 1
                bar.src += ")"
                continue
            if m.group("loosetie"):
                # Detached tie (the HOW HIGH `-AG` case): attach to the
                # previous pitched note.
                target = None
                for sec in reversed(sections):
                    for b in reversed(sec.bars):
                        for n in reversed(b.notes):
                            target = n
                            break
                        if target:
                            break
                    if target:
                        break
                for n in reversed(bar.notes):
                    target = n
                    break
                if target is None:
                    raise ParseError(f"body line {line_no}: tie with no note")
                target.tie = True
                bar.src += "-"
                prev_was_note_no_ws = False
                continue
            # note or rest
            letter = m.group("letter")
            marks = m.group("marks")
            if letter in "zx":
                octave = 0
                up = letter
            else:
                up = letter.upper()
                octave = 5 if letter.islower() else 4
                octave += marks.count("'") - marks.count(",")
            units = Fraction(int(m.group("len") or 1))
            if m.group("frac"):
                units /= Fraction(int(m.group("frac")[1:] or 2))
            if tuplet_left:
                units *= Fraction(2, 3)
                tuplet_left -= 1
            bar.notes.append(Note(
                letter=up,
                accidental=m.group("acc") or "",
                octave=octave,
                units=units,
                tie=bool(m.group("tie")),
                beamed_prev=prev_was_note_no_ws,
                src=m.group("note"),
            ))
            bar.src += m.group("note")
            prev_was_note_no_ws = True
    close_bar("|")
    if tuplet_left:
        raise ParseError("unterminated (3 tuplet at end of tune")
    if slur_depth:
        raise ParseError("unclosed slur '(' at end of tune")
    sections = [s for s in sections if s.bars or s.label is not None]
    return Tune(
        header_lines=header_lines,
        headers=headers,
        meter_units=meter_units,
        unit_len=unit_len,
        key=headers["K"].strip(),
        sections=sections,
    )


# ---------------------------------------------------------------- checks

# F3..C6 (plan §5 check 4)
_RANGE_LO = 3 * 7 + _LETTERS.index("F")
_RANGE_HI = 6 * 7 + _LETTERS.index("C")


def effective_pickup(tune: Tune) -> Bar | None:
    """The tune's anacrusis bar, wherever the house style put it.

    A pickup is always a *short* bar (< meter). Two verified layouts exist:
    before the first label (CLOSE YOUR EYES: `c3 G ||"^A" …`) and as a short
    first bar inside the first labeled section (COTTAGE FOR SALE: `"^A" B | …`).
    """
    prelude = tune.prelude
    if prelude is not None and prelude.units < tune.meter_units:
        return prelude
    body = tune.body_sections
    if body and body[0].bars and body[0].bars[0].units < tune.meter_units:
        return body[0].bars[0]
    return None


def validate_tune(
    text: str,
    plan: list[tuple[str, int]] | None = None,
) -> tuple[Tune | None, Report]:
    """Validate ABC text; `plan` = expected [(section label, bar count)]."""
    report = Report()
    try:
        tune = parse_tune(text)
    except ParseError as exc:
        report.errors.append(Finding("parse", "-", str(exc)))
        return None, report

    meter = tune.meter_units
    pickup = effective_pickup(tune)
    bar_no = 0
    for sec in tune.sections:
        for i, bar in enumerate(sec.bars):
            bar_no += 1
            where = f"bar {bar_no}" + (f" ({sec.label} {i + 1})" if sec.label else "")
            total = bar.units
            if bar is pickup:
                pass  # anacrusis: any short length is fine
            elif total != meter:
                report.errors.append(Finding(
                    "barsum", where,
                    f"sums to {total}, meter is {meter}: {bar.src!r}"))
            for n in bar.notes:
                if n.is_rest:
                    continue
                if not (_RANGE_LO <= n.step <= _RANGE_HI):
                    report.errors.append(Finding(
                        "range", where, f"pitch out of F3..C6: {n.src!r}"))

    _check_ties(tune, report)
    if plan is not None:
        _check_plan(tune, plan, report)
    _check_beaming(tune, report)
    _check_leaps(tune, report)
    return tune, report


def section_bar_counts(tune: Tune) -> list[tuple[str, int]]:
    """Per-section full-bar counts, the anacrusis excluded wherever it sits."""
    pickup = effective_pickup(tune)
    return [
        (sec.label or "", sum(1 for b in sec.bars if b is not pickup))
        for sec in tune.body_sections
    ]


def _check_plan(tune: Tune, plan: list[tuple[str, int]], report: Report) -> None:
    counts = section_bar_counts(tune)
    if len(counts) != len(plan):
        report.errors.append(Finding(
            "sections", "-",
            f"{len(counts)} sections, plan expects {len(plan)}: "
            f"got {[l for l, _ in counts]}, want {[l for l, _ in plan]}"))
        return
    for (got_label, got_bars), (label, bars) in zip(counts, plan):
        if got_bars != bars:
            report.errors.append(Finding(
                "barcount", f"section {got_label}",
                f"{got_bars} bars, plan expects {bars}"))
        if got_label != label:
            report.warnings.append(Finding(
                "label", f"section {got_label}",
                f"plan expects label {label!r}"))


def _check_ties(tune: Tune, report: Report) -> None:
    notes = [n for b in tune.all_bars for n in b.notes]
    for i, n in enumerate(notes):
        if not n.tie:
            continue
        if n.is_rest:
            report.errors.append(Finding("tie", n.src, "tie on a rest"))
            continue
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        if nxt is None or nxt.is_rest:
            report.errors.append(Finding(
                "tie", n.src, "tie has no following note"))
        elif (nxt.letter, nxt.octave) != (n.letter, n.octave):
            report.errors.append(Finding(
                "tie", n.src,
                f"tie target {nxt.src!r} is a different pitch"))


def _check_beaming(tune: Tune, report: Report) -> None:
    """Reject v2-style output where no eighth note is ever beamed."""
    eighths = beamed = 0
    notes = [n for b in tune.all_bars for n in b.notes]
    for i, n in enumerate(notes):
        if n.is_rest or n.units >= 2:
            continue
        eighths += 1
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        if n.beamed_prev or (nxt is not None and nxt.beamed_prev):
            beamed += 1
    if eighths >= 8 and beamed == 0:
        report.warnings.append(Finding(
            "beaming", "-",
            f"{eighths} sub-quarter notes and none adjacent — flat v2-style "
            "spacing; reproduce the manuscript's beam groups"))


def _check_leaps(tune: Tune, report: Report) -> None:
    prev: Note | None = None
    for bar in tune.all_bars:
        for n in bar.notes:
            if n.is_rest:
                prev = None
                continue
            if prev is not None and abs(n.step - prev.step) > 7:
                report.warnings.append(Finding(
                    "leap", n.src,
                    f"leap of {abs(n.step - prev.step)} steps from {prev.src!r}"))
            prev = n
