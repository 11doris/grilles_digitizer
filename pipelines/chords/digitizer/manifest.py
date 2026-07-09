"""Discover work units from the crop PNGs themselves.

The filenames are the source of truth: `<page>_<index>_<TITLE_SLUG>.png`
(e.g. `100_01_DINAH.png`). Renaming a crop is therefore all it takes to fix
its title — no bookkeeping file has to be kept in sync.

The upstream `manifest.csv` is used only as an optional lookup to restore
the original spelling of a title (apostrophes, hyphens, accents) that the
filename slug cannot encode: a slug that matches a slugified manifest
`title`/`alt_title` gets that spelling; anything else falls back to
underscores→spaces plus common-contraction heuristics.
"""

from __future__ import annotations

import csv
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkUnit:
    """One cropped PNG to transcribe.

    `page` and `title` are derived from the filename (spec §2.2); the
    manifest, when present, only improves the title's spelling.
    """

    current_file: str
    page: int
    title: str

    @property
    def stem(self) -> str:
        return Path(self.current_file).stem


_FILENAME_RE = re.compile(r"^(\d+)_(\d+)_(.+)\.png$", re.IGNORECASE)

# Contractions the slug flattens; applied to the spaced slug as a fallback
# when the manifest has no original spelling for it.
_CONTRACTIONS = (
    (re.compile(r"\b(I) M\b"), r"\1'M"),
    (re.compile(r"\bIM\b"), "I'M"),
    (re.compile(r"\b(I) D\b"), r"\1'D"),
    (re.compile(r"\b(\w+) (LL|VE|RE)\b"), r"\1'\2"),
    (
        re.compile(
            r"\b(CAN|DON|WON|AIN|ISN|DIDN|DOESN|WASN|WEREN|HAVEN|COULDN|WOULDN|SHOULDN) T\b"
        ),
        r"\1'T",
    ),
    (re.compile(r"\b(IT|THAT|WHAT|HE|SHE|LET|THERE|WHO|HERE) S\b"), r"\1'S"),
    (re.compile(r"\bO CLOCK\b"), "O'CLOCK"),
)


def slugify(title: str) -> str:
    """Mirror of crop_tunes.slugify — must stay identical so manifest titles
    round-trip to the filenames that stage produced."""
    t = title.upper().strip()
    t = t.replace("&", " AND ")
    t = re.sub(r"[^A-Z0-9]+", "_", t)
    return t.strip("_")


def _title_from_slug(slug: str, known: dict[str, str]) -> str:
    original = known.get(slug)
    if original is not None:
        return original
    title = slug.upper().replace("_", " ")
    for pattern, replacement in _CONTRACTIONS:
        title = pattern.sub(replacement, title)
    return title


def _decode_manifest(path: Path) -> str:
    """Decode the manifest, tolerating non-UTF-8 (the book's titles carry French
    accents; the upstream CSV is commonly cp1252)."""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")  # never raises


def _known_titles(manifest: Path | None) -> dict[str, str]:
    """slug -> original spelling, pooled from the manifest's title/alt_title."""
    if manifest is None or not manifest.is_file():
        return {}
    known: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(_decode_manifest(manifest)))
    for row in reader:
        for column in ("title", "alt_title"):
            title = (row.get(column) or "").strip()
            if title:
                known.setdefault(slugify(title), title)
    return known


def load_units(crops_dir: Path, manifest: Path | None = None) -> list[WorkUnit]:
    """Work units from `<page>_<index>_<SLUG>.png` files, in (page, index) order.
    Non-conforming PNG names are skipped with a warning."""
    known = _known_titles(manifest)
    keyed: list[tuple[int, int, str, WorkUnit]] = []
    for path in crops_dir.glob("*.png"):
        m = _FILENAME_RE.match(path.name)
        if not m:
            print(f"warning: skipping unrecognized crop name: {path.name}", file=sys.stderr)
            continue
        page, index, slug = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        unit = WorkUnit(
            current_file=path.name,
            page=page,
            title=_title_from_slug(slug, known),
        )
        keyed.append((page, index, path.name, unit))
    keyed.sort(key=lambda k: k[:3])
    return [unit for _, _, _, unit in keyed]
