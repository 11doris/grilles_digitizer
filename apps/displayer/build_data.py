#!/usr/bin/env python3
"""
Bundle the tune index + digitized tunes into data/tunes_data.js.

Driven by data/title_index.csv (single source of truth, one record per row):
each row pairs a chord scan (data/chords/01_crops/<chords_file>) with a melody
scan (data/melody/01_crops/<melody_file>). A chord matched to several melody
sheets recurs once per sheet; those rows merge into one record whose
melody_images lists every sheet. Rows whose chord tune has been
digitized + annotated (data/chords/05_annotated/<id>.json) get the full chord
JSON embedded — including its key, section_keys and harmonic_fingerprint;
rows whose melody has been digitized get the ABC source embedded — the join
is by melody scan stem: data/melody/04_verified/<stem of melody_file>.abc. Any
new .abc saved there is included automatically at the next build.

Referenced scans are copied into apps/displayer/crops/ and
apps/displayer/melody_crops/ so the deployed app (GitHub Pages uploads
only apps/displayer/) can show them. Sources are expected to already be
1-bit PNGs (see docs/specs/melody_digitizer_spec.md, output-encoding
convention); any that aren't are re-encoded here with a warning.

Usage
-----
    python apps/displayer/build_data.py
    python apps/displayer/build_data.py --index data/title_index.csv \
        --tunes-dir data/chords/05_annotated --crops-dir data/chords/01_crops \
        --melody-crops-dir data/melody/01_crops --melodies-dir data/melody/04_verified

    manual deployment:
    git checkout main
    git pull
    git subtree push --prefix=apps/displayer origin gh-pages
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
    root = here.parents[1]  # repo root
    parser.add_argument("--index", default=str(root / "data" / "title_index.csv"),
                        help="Title index CSV (default: data/title_index.csv)")
    parser.add_argument("--tunes-dir", default=str(root / "data" / "chords" / "05_annotated"),
                        help="Annotated chord tune JSONs (default: data/chords/05_annotated)")
    parser.add_argument("--fallback-tunes-dir",
                        default=str(root / "data" / "chords" / "04_verified"),
                        help="Verified tunes without an annotation yet — bundled "
                             "without key-dependent features (spec §8.1)")
    parser.add_argument("--similarity-dir",
                        default=str(root / "data" / "chords" / "06_similarity"),
                        help="Similarity output; its displayer_similar.json is "
                             "bundled into data/similar_data.js when present")
    parser.add_argument("--crops-dir", default=str(root / "data" / "chords" / "01_crops"),
                        help="Chord scan PNGs (default: data/chords/01_crops)")
    parser.add_argument("--melody-crops-dir", default=str(root / "data" / "melody" / "01_crops"),
                        help="Melody scan PNGs (default: data/melody/01_crops)")
    parser.add_argument("--melodies-dir", default=str(root / "data" / "melody" / "04_verified"),
                        help="Verified melody ABC files (default: data/melody/04_verified; "
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
    # Annotated files win; a verified tune without an annotation yet falls
    # back to its 04_verified source, so it still displays — its
    # key-dependent features are simply absent (spec §8.1).
    digitized: dict[str, dict] = {}
    for source_dir in (Path(args.fallback_tunes_dir), tunes_dir):
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.glob("*.json")):
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
    by_id: dict[str, dict] = {}
    with open(index_path, newline="", encoding="utf-8") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            chords_file = (row.get("chords_file") or "").strip()
            melody_file = (row.get("melody_file") or "").strip()
            if not chords_file and not melody_file:
                warnings.append(f"{index_path.name}:{lineno}: row with no files, skipped")
                continue
            tune_id = Path(chords_file or melody_file).stem

            record = by_id.get(tune_id)
            if record is None:
                record = {"id": tune_id}
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
                record["has_chord_json"] = tune_json is not None
                record["has_melody_abc"] = False
                if tune_json is not None:
                    record["tune"] = tune_json
                by_id[tune_id] = record
                tunes.append(record)
            elif not (chords_file and melody_file):
                warnings.append(f"{index_path.name}:{lineno}: duplicate id {tune_id}, skipped")
                continue
            # else: a chord matched to several melody sheets recurs once per
            # sheet — attach the extra melody to the existing record below.

            if not melody_file:
                continue
            name = sync_scan(melody_crops_dir / melody_file, out_melody, warnings)
            if name:
                path = f"melody_crops/{name}"
                imgs = record.setdefault("melody_images", [])
                if path in imgs:
                    warnings.append(f"{index_path.name}:{lineno}: duplicate melody "
                                    f"{name} for {tune_id}, skipped")
                    continue
                imgs.append(path)
                record.setdefault("melody_image", path)  # first sheet = primary
            else:
                warnings.append(f"missing melody scan: {melody_crops_dir / melody_file}")
            abc = melodies.pop(Path(melody_file).stem, None)
            if abc is not None:
                if record.get("abc") is None:
                    record["abc"] = abc
                    record["has_melody_abc"] = True
                else:
                    warnings.append(f"{tune_id}: second melody ABC not bundled: "
                                    f"{Path(melody_file).stem}.abc")

    for stem in digitized:  # verified tunes the index doesn't know about
        warnings.append(f"digitized tune not in index (not bundled): {stem}.json")
    for stem in melodies:  # ABC files matching no index row's melody_file
        warnings.append(f"melody ABC not in index (not bundled): {stem}.abc")

    # Drop stale copies of scans no longer referenced.
    keep_by_dir = (
        (out_crops, {Path(t["chord_image"]).name for t in tunes if "chord_image" in t}),
        (out_melody, {Path(p).name for t in tunes for p in t.get("melody_images", ())}),
    )
    for out_dir, keep in keep_by_dir:
        if out_dir.is_dir():
            for stale in out_dir.glob("*.png"):
                if stale.name not in keep:
                    stale.unlink()

    # melody_images only carries information beyond melody_image when a chord
    # has several sheets; drop the singleton lists to keep the bundle lean.
    for t in tunes:
        if len(t.get("melody_images", ())) < 2:
            t.pop("melody_images", None)

    tunes.sort(key=lambda t: str(t["title"]).upper())

    out_path = here / "data" / "tunes_data.js"
    out_path.parent.mkdir(exist_ok=True)
    # Minified: this bundle is parsed on every page load (mobile included)
    # and grows with every digitized tune — never ship it pretty-printed.
    payload = json.dumps(tunes, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(f"window.TUNES = {payload};\n", encoding="utf-8")

    # Similarity bundle (spec §8.1): compact top-K suggestions per tune.
    # Only digitized tunes appear as suggestions by construction. Hard size
    # guard (spec §6.4/§10): fail the build rather than truncate silently.
    SIMILAR_LIMIT_MB = 2.0
    similar_path = Path(args.similarity_dir) / "displayer_similar.json"
    similar_out = here / "data" / "similar_data.js"
    if similar_path.is_file():
        similar = json.loads(similar_path.read_text(encoding="utf-8"))
        similar = {k: v for k, v in similar.items() if k in by_id}
        similar_out.write_text(
            "window.SIMILAR = " + json.dumps(similar, ensure_ascii=False,
                                             separators=(",", ":")) + ";\n",
            encoding="utf-8")
        size_mb = similar_out.stat().st_size / 1e6
        if size_mb > SIMILAR_LIMIT_MB:
            print(f"ERROR: similar_data.js is {size_mb:.2f} MB "
                  f"(> {SIMILAR_LIMIT_MB} MB guard) — reduce the engine's "
                  "displayer top-K instead of shipping this", file=sys.stderr)
            return 1
        print(f"Wrote {len(similar)} tunes' suggestions to "
              f"{similar_out.relative_to(here)} ({size_mb:.2f} MB)")
    else:
        similar_out.write_text("window.SIMILAR = {};\n", encoding="utf-8")
        warnings.append(f"no similarity output at {similar_path} — "
                        "suggestions disabled (empty similar_data.js)")

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
