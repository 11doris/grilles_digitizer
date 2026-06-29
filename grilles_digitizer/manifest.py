"""Read the upstream manifest.csv — one work unit per row."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkUnit:
    """One cropped PNG to transcribe, plus the manifest context for it.

    Only `current_file`, `page`, and `title` are used (spec §2.2); any other
    manifest columns (e.g. `review`, `conf`) are ignored.
    """

    current_file: str
    page: int
    title: str

    @property
    def stem(self) -> str:
        return Path(self.current_file).stem


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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


def load_manifest(path: Path) -> list[WorkUnit]:
    """Load work units in manifest order. Rows without a current_file are skipped."""
    units: list[WorkUnit] = []
    reader = csv.DictReader(io.StringIO(_decode_manifest(path)))
    for row in reader:
        current_file = (row.get("current_file") or "").strip()
        if not current_file:
            continue
        units.append(
            WorkUnit(
                current_file=current_file,
                page=_to_int(row.get("page", "")),
                title=(row.get("title") or "").strip(),
            )
        )
    return units
