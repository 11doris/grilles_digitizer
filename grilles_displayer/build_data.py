#!/usr/bin/env python3
"""
Bundle the tune index + digitized tunes into data/tunes_data.js.

Driven by title_index.csv (single source of truth, one record per row):
each row pairs a chord scan (crops/<chords_file>) with a melody scan
(melody_crops/<melody_file>). Rows whose chord tune has been digitized
(tunes_verified/<id>.json) get the full chord JSON embedded; rows whose
melody has been digitized get the ABC source embedded — the join is by
melody scan stem: melodies_verified/<stem of melody_file>.abc. Any new
.abc saved there is included automatically at the next build.

Referenced scans are copied into grilles_displayer/crops/ and
grilles_displayer/melody_crops/ so the deployed app (GitHub Pages uploads
only grilles_displayer/) can show them. Sources are expected to already be
1-bit PNGs (see Instructions/melody_digitizer_spec.md, output-encoding
convention); any that aren't are re-encoded here with a warning.

Usage
-----
    python grilles_displayer/build_data.py
    python grilles_displayer/build_data.py --index ./title_index.csv \
        --tunes-dir ./tunes_verified --crops-dir ./crops \
        --melody-crops-dir ./melody_crops --melodies-dir ./melodies_verified

    manual deployment:
    git checkout main
    git pull
    git subtree push --prefix=grilles_displayer origin gh-pages
    git checkout main
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

IGNORED_STEMS = frozenset({"run_report", "run_state", "verification_state"})


def title_case(slug: str) -> str:
    """FILENAME_STYLE -> Title Case ('AIN_T' -> 'Ain T' is accepted as-is)."""
    return " ".join(w.capitalize() for w in slug.split("_") if w)


def sync_scan(src: Path, out_dir: Path, warnings: list[str]) -> str | None:
    """Copy a scan into the app folder, expecting 1-bit; returns the file name.

    Skips work when the destination already carries the source's mtime.
    A non-1-bit source is re-encoded (grayscale -> threshold 128 -> 1-bit
    optimized PNG) so a stray legacy crop cannot bloat the deploy.
    """
    if not src.is_file():
        return None
    dst = out_dir / src.name
    st = src.stat()
    if dst.is_file() and dst.stat().st_mtime_ns == st.st_mtime_ns:
        return src.name  # up to date
    out_dir.mkdir(exist_ok=True)
    from PIL import Image
    with Image.open(src) as im:
        if im.mode == "1":
            shutil.copy2(src, dst)  # preserves mtime
            return src.name
        warnings.append(f"re-encoded non-1-bit scan: {src}  (mode {im.mode})")
        if im.mode != "L":
            im = im.convert("L")
        im.point(lambda x: 255 if x > 128 else 0).convert("1").save(
            dst, "PNG", optimize=True)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))  # enable the skip check
    return src.name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    root = here.parent
    parser.add_argument("--index", default=str(root / "title_index.csv"),
                        help="Title index CSV (default: ../title_index.csv)")
    parser.add_argument("--tunes-dir", default=str(root / "tunes_verified"),
                        help="Verified chord tune JSONs (default: ../tunes_verified)")
    parser.add_argument("--crops-dir", default=str(root / "crops"),
                        help="Chord scan PNGs (default: ../crops)")
    parser.add_argument("--melody-crops-dir", default=str(root / "melody_crops"),
                        help="Melody scan PNGs (default: ../melody_crops)")
    parser.add_argument("--melodies-dir", default=str(root / "melodies_verified"),
                        help="Verified melody ABC files (default: ../melodies_verified; "
                             "may not exist yet)")
    args = parser.parse_args()

    index_path = Path(args.index)
    if not index_path.is_file():
        print(f"ERROR: title index not found: {index_path}", file=sys.stderr)
        return 1
    tunes_dir = Path(args.tunes_dir)
    crops_dir = Path(args.crops_dir)
    melody_crops_dir = Path(args.melody_crops_dir)
    melodies_dir = Path(args.melodies_dir)
    out_crops = here / "crops"
    out_melody = here / "melody_crops"

    # Digitized chord tunes, keyed by file stem (= chords_file stem).
    digitized: dict[str, dict] = {}
    if tunes_dir.is_dir():
        for path in sorted(tunes_dir.glob("*.json")):
            if path.stem in IGNORED_STEMS or path.stem.endswith("_opus"):
                continue
            try:
                digitized[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"ERROR: failed to read {path.name}: {exc}", file=sys.stderr)
                return 1

    # Digitized melodies (ABC), keyed by melody scan stem (= melody_file stem).
    melodies: dict[str, str] = {}
    if melodies_dir.is_dir():
        for path in sorted(melodies_dir.glob("*.abc")):
            try:
                melodies[path.stem] = path.read_text(encoding="utf-8")
            except OSError as exc:
                print(f"ERROR: failed to read {path.name}: {exc}", file=sys.stderr)
                return 1

    warnings: list[str] = []
    tunes: list[dict] = []
    seen_ids: set[str] = set()
    with open(index_path, newline="", encoding="utf-8") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            chords_file = (row.get("chords_file") or "").strip()
            melody_file = (row.get("melody_file") or "").strip()
            if not chords_file and not melody_file:
                warnings.append(f"{index_path.name}:{lineno}: row with no files, skipped")
                continue
            tune_id = Path(chords_file or melody_file).stem
            if tune_id in seen_ids:
                warnings.append(f"{index_path.name}:{lineno}: duplicate id {tune_id}, skipped")
                continue
            seen_ids.add(tune_id)

            record: dict = {"id": tune_id}
            tune_json = digitized.pop(tune_id, None)
            slug = (row.get("chords_title") or row.get("melody_title") or "").strip()
            record["title"] = ((tune_json or {}).get("title")
                               or title_case(slug) or tune_id)
            if chords_file:
                name = sync_scan(crops_dir / chords_file, out_crops, warnings)
                if name:
                    record["chord_image"] = f"crops/{name}"
                else:
                    warnings.append(f"missing chord scan: {crops_dir / chords_file}")
            if melody_file:
                name = sync_scan(melody_crops_dir / melody_file, out_melody, warnings)
                if name:
                    record["melody_image"] = f"melody_crops/{name}"
                else:
                    warnings.append(f"missing melody scan: {melody_crops_dir / melody_file}")
            abc = melodies.pop(Path(melody_file).stem, None) if melody_file else None
            record["has_chord_json"] = tune_json is not None
            record["has_melody_abc"] = abc is not None
            if tune_json is not None:
                record["tune"] = tune_json
            if abc is not None:
                record["abc"] = abc
            tunes.append(record)

    for stem in digitized:  # verified tunes the index doesn't know about
        warnings.append(f"digitized tune not in index (not bundled): {stem}.json")
    for stem in melodies:  # ABC files matching no index row's melody_file
        warnings.append(f"melody ABC not in index (not bundled): {stem}.abc")

    # Drop stale copies of scans no longer referenced.
    for out_dir, key in ((out_crops, "chord_image"), (out_melody, "melody_image")):
        if out_dir.is_dir():
            keep = {Path(t[key]).name for t in tunes if key in t}
            for stale in out_dir.glob("*.png"):
                if stale.name not in keep:
                    stale.unlink()

    tunes.sort(key=lambda t: str(t["title"]).upper())

    out_path = here / "data" / "tunes_data.js"
    out_path.parent.mkdir(exist_ok=True)
    payload = json.dumps(tunes, ensure_ascii=False, indent=1)
    out_path.write_text(f"window.TUNES = {payload};\n", encoding="utf-8")

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    n_chord_json = sum(1 for t in tunes if t["has_chord_json"])
    n_melody_abc = sum(1 for t in tunes if t["has_melody_abc"])
    n_chord_png = sum(1 for t in tunes if "chord_image" in t)
    n_melody_png = sum(1 for t in tunes if "melody_image" in t)
    print(f"Wrote {len(tunes)} tunes to {out_path.relative_to(here)} "
          f"({n_chord_json} chord-JSON, {n_melody_abc} melody-ABC; "
          f"{n_chord_png} chord PNGs, {n_melody_png} melody PNGs; "
          f"{len(warnings)} warnings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
