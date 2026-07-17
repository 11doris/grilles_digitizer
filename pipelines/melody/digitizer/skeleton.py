"""Deterministic skeleton: ABC headers + unrolled section plan (plan §3).

Everything comes from the annotated chords JSON except `K:`, which is the
*printed* signature — derivable as the tonic for major-mode tunes, but
reduced-signature minor tunes (CLOSE YOUR EYES: F minor printed with one
flat, `K:F`) need the read pass (or an override) to report it.

`data/melody/overrides/<melody-stem>.json` records explicit operator
decisions where the melody manuscript's structure differs from the grille
(owner decision #4, 2026-07-17 — never silent skips):

    {
      "note": "why this override exists",
      "skip_strains": ["intro"],           // strains absent from the melody ms
      "section_labels": ["A","A","B","A"], // printed labels, replaces derived
      "bar_counts": [8, 8, 8, 10],         // replaces strains-derived counts
      "printed_key": "F"                   // printed signature
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .config import Config
from .manifest import MelodyUnit


@dataclass(frozen=True)
class PlanSection:
    label: str
    bars: int
    strain: str
    chords: tuple[str, ...]  # per-bar anchor text, e.g. "Gm7b5 / C(b9)"


@dataclass
class Skeleton:
    unit: MelodyUnit
    header_lines: list[str]
    sections: list[PlanSection]
    meter: str
    meter_units: Fraction
    printed_key: str
    needs_printed_key: bool  # True: K: is a guess, the read pass must confirm
    key_tonic: str
    key_mode: str
    title: str
    composer: str | None
    year: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def plan(self) -> list[tuple[str, int]]:
        return [(s.label, s.bars) for s in self.sections]

    @property
    def total_bars(self) -> int:
        return sum(s.bars for s in self.sections)


def load_override(unit: MelodyUnit, cfg: Config) -> dict | None:
    path = unit.override_path(cfg)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _bar_chords(bar: dict) -> str:
    beats = bar.get("beats", {})
    return " / ".join(beats[k] for k in sorted(beats, key=int))


def _derive_labels(parts: list[tuple[str, str]]) -> list[str]:
    """Occurrence-numbered labels from (strain, part-label) pairs.

    Primes fold into the numbering: within a strain, the family of a part
    label is the label with primes stripped; the first occurrence keeps the
    bare family name, the n-th gets a suffix n (A, A' , B, A -> A A1 B A2).
    Non-chorus strains are prefixed (verse_A); a part whose label equals the
    strain name (Intro, Coda) becomes just the strain name lowercased.
    """
    seen: dict[tuple[str, str], int] = {}
    labels: list[str] = []
    for strain, part_label in parts:
        if part_label.lower() == strain.lower():
            labels.append(strain.lower())
            continue
        family = part_label.rstrip("'")
        n = seen.get((strain, family), 0)
        seen[(strain, family)] = n + 1
        label = family if n == 0 else f"{family}{n}"
        if strain != "chorus":
            label = f"{strain}_{label}"
        labels.append(label)
    return labels


def build_skeleton(
    unit: MelodyUnit,
    cfg: Config,
    printed_key: str | None = None,
) -> Skeleton:
    """Headers + section plan for one unit. `printed_key` is the read pass's
    (or caller's) report of the printed signature; overrides take precedence.
    """
    data = json.loads(unit.chords_path(cfg).read_text(encoding="utf-8"))
    override = load_override(unit, cfg)
    notes: list[str] = []

    skip = set()
    if override:
        skip = {s.lower() for s in override.get("skip_strains", ())}
        notes.append(f"override applied: {unit.override_path(cfg).name}")
        for s in sorted(skip):
            notes.append(f"override: strain {s!r} skipped ({override.get('note', 'no note')})")

    parts: list[tuple[str, str, list[dict]]] = []  # (strain, label, bars)
    for strain in data["strains"]:
        name = strain["name"]
        if name.lower() in skip:
            continue
        for part in strain["parts"]:
            parts.append((name, part["label"], part["bars"]))

    labels = _derive_labels([(s, l) for s, l, _ in parts])
    counts = [len(bars) for _, _, bars in parts]
    if override and "section_labels" in override:
        got = override["section_labels"]
        if len(got) != len(labels):
            raise ValueError(
                f"{unit.stem}: override section_labels has {len(got)} entries, "
                f"plan has {len(labels)} sections")
        labels = list(got)
    if override and "bar_counts" in override:
        got = override["bar_counts"]
        if len(got) != len(counts):
            raise ValueError(
                f"{unit.stem}: override bar_counts has {len(got)} entries, "
                f"plan has {len(counts)} sections")
        counts = list(got)

    sections = [
        PlanSection(
            label=label,
            bars=count,
            strain=strain,
            chords=tuple(_bar_chords(b) for b in bars),
        )
        for label, count, (strain, _, bars) in zip(labels, counts, parts)
    ]

    key = data.get("key") or {}
    tonic, mode = key.get("tonic", "C"), key.get("mode", "major")
    needs_printed_key = False
    if override and override.get("printed_key"):
        k = override["printed_key"]
    elif printed_key:
        k = printed_key
    elif mode == "major":
        k = tonic
    else:
        # Reduced-signature minor tunes make this underivable; the read pass
        # must confirm what the page prints.
        k = f"{tonic}m"
        needs_printed_key = True
        notes.append(f"K: guessed {k!r} from {tonic} {mode}; confirm printed signature")

    title = data["title"]
    composer = data.get("composer")
    year = data.get("year")
    style = (data.get("style") or "").lower()
    tempo = (data.get("tempo") or "").lower()
    source = data.get("source", "Anthologie des grilles de jazz")
    meter = data.get("time_signature", "4/4")

    header_lines = ["X:1", f"T:{title}"]
    if composer:
        header_lines.append(f"C:{composer} ({year})" if year else f"C:{composer}")
    header_lines.append(
        f"O:Chords: {source}, p. {data.get('page', unit.chords_page)} "
        f"(data/chords/05_annotated/{unit.chords_file}). "
        f"Melody: AGJ melody ms (data/melody/01_crops/{unit.melody_file})."
    )
    header_lines.append(f"R:{style}, {tempo}" if tempo else f"R:{style}")
    header_lines.append(f"M:{meter}")
    header_lines.append("L:1/8")
    header_lines.append(f"K:{k}")

    num, den = meter.split("/")
    meter_units = Fraction(int(num), int(den)) / Fraction(1, 8)

    return Skeleton(
        unit=unit,
        header_lines=header_lines,
        sections=sections,
        meter=meter,
        meter_units=meter_units,
        printed_key=k,
        needs_printed_key=needs_printed_key,
        key_tonic=tonic,
        key_mode=mode,
        title=title,
        composer=composer,
        year=year,
        notes=notes,
    )
