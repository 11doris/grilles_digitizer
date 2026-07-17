"""Work-unit discovery: melody crops ⋈ title_index ⋈ annotated chords JSON.

A tune is processable when all three exist (plan §2):

1. the melody crop `data/melody/01_crops/<melody-stem>.png`
2. a `match_status == both` row in `data/title_index.csv`
3. the chords JSON `data/chords/05_annotated/<chords-stem>.json`

An optional `data/melody/overrides/<melody-stem>.json` records explicit
operator decisions for tunes whose melody structure differs from the grille
(skip strains, replace labels/bar counts, printed key) — see skeleton.py.
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config

_FILENAME_RE = re.compile(r"^(\d+)_(\d+)_(.+)\.png$", re.IGNORECASE)


@dataclass(frozen=True)
class MelodyUnit:
    """One melody crop with its joined chords JSON."""

    melody_file: str  # e.g. 149_01_CLOSE_YOUR_EYES.png
    melody_page: int
    melody_index: int
    chords_file: str  # e.g. 77_01_CLOSE_YOUR_EYES.json
    chords_page: int

    @property
    def stem(self) -> str:
        return Path(self.melody_file).stem

    @property
    def chords_stem(self) -> str:
        return Path(self.chords_file).stem

    def crop_path(self, cfg: Config) -> Path:
        return cfg.crops_dir / self.melody_file

    def chords_path(self, cfg: Config) -> Path:
        return cfg.chords_dir / self.chords_file

    def override_path(self, cfg: Config) -> Path:
        return cfg.overrides_dir / f"{self.stem}.json"


@dataclass(frozen=True)
class DiscoveryStats:
    index_rows_both: int
    missing_crop: tuple[str, ...]  # index rows whose melody PNG is absent
    missing_chords: tuple[str, ...]  # index rows whose chords JSON is absent
    units: int


def load_units(cfg: Config) -> tuple[list[MelodyUnit], DiscoveryStats]:
    """Processable units in (melody page, index) order, plus discovery stats."""
    units: list[MelodyUnit] = []
    missing_crop: list[str] = []
    missing_chords: list[str] = []
    rows_both = 0
    with open(cfg.title_index, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["match_status"] != "both":
                continue
            rows_both += 1
            melody_file = row["melody_file"]
            m = _FILENAME_RE.match(melody_file)
            if not m:
                print(f"warning: unrecognized melody_file in index: {melody_file}",
                      file=sys.stderr)
                continue
            if not (cfg.crops_dir / melody_file).is_file():
                missing_crop.append(melody_file)
                continue
            chords_file = Path(row["chords_file"]).stem + ".json"
            if not (cfg.chords_dir / chords_file).is_file():
                missing_chords.append(melody_file)
                continue
            units.append(MelodyUnit(
                melody_file=melody_file,
                melody_page=int(m.group(1)),
                melody_index=int(m.group(2)),
                chords_file=chords_file,
                chords_page=int(row["chords_page"]),
            ))
    units.sort(key=lambda u: (u.melody_page, u.melody_index))
    stats = DiscoveryStats(
        index_rows_both=rows_both,
        missing_crop=tuple(missing_crop),
        missing_chords=tuple(missing_chords),
        units=len(units),
    )
    return units, stats


def unit_for_stem(units: list[MelodyUnit], stem: str) -> MelodyUnit:
    for unit in units:
        if unit.stem == stem:
            return unit
    raise KeyError(f"no processable unit for melody stem {stem!r}")
