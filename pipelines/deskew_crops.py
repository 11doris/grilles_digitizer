"""Deskew one or all crop PNGs in place (chords or melody).

Straightens page-level rotation in cropped tunes by finding the small rotation
that packs the ink into the sharpest horizontal rows -- staff lines (melody) and
grid rules (chords) are long horizontal strokes, so both pile their ink into a
few rows only when level. Reuses the chords/melody pipeline primitives so the
result matches what `melody_cropper` produces at crop time.

Accepts a single PNG, a glob, or a directory (deskews every *.png inside).

  python pipelines/deskew_crops.py data/chords/crops
  python pipelines/deskew_crops.py data/melody/crops
  python pipelines/deskew_crops.py data/melody/crops/247_02_FLYING_HOME.png
  python pipelines/deskew_crops.py "data/chords/crops/100_*.png" --dry-run

Options:
  --max-deg  widest rotation searched, +/- deg           (default 6.0)
  --step     search step in deg                          (default 0.1)
  --min-angle  below this the crop is left untouched     (default 0.1)
  --backup   keep the original as <name>.orig.png
  --dry-run  report estimated angles, write nothing

CAVEAT: crops are stored 1-bit, so rotating one and re-binarizing loses a little
edge sharpness. Re-deskewing an already-level crop is a no-op (skipped by
--min-angle), but do NOT run this twice on the same slanted crop -- if a crop is
still off after one pass, regenerate it from the PDF with melody_cropper /
crop_tunes instead. Run --dry-run first to see what would change.
"""
import argparse
import glob
import os
import shutil
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
sys.path.insert(0, ROOT)

from pipelines.chords.crop_tunes import to_ink, write_png_1bit
from pipelines.melody.melody_cropper import deskew, estimate_skew


def collect_inputs(inputs):
    """Positional args -> list of crop PNGs (files, globs, or directories).

    Skips the `.orig.png` backups this script writes so a re-run doesn't
    process (and re-rotate) them."""
    out = []
    for spec in inputs:
        if os.path.isdir(spec):
            out += sorted(glob.glob(os.path.join(spec, "*.png")))
        elif any(ch in spec for ch in "*?["):
            out += sorted(glob.glob(spec))
        else:
            out.append(spec)
    return [p for p in out if not p.endswith(".orig.png")]


def process(src, max_deg, step, min_angle, backup, dry_run):
    """Estimate + correct skew for one crop. Returns (angle, changed)."""
    native = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
    if native is None:
        raise IOError(f"could not read {src}")
    _, ink = to_ink(native)
    angle = estimate_skew(ink, max_deg=max_deg, step=step)
    if abs(angle) < min_angle or dry_run:
        return angle, False
    if backup:
        shutil.copy2(src, f"{os.path.splitext(src)[0]}.orig.png")
    write_png_1bit(src, deskew(native, angle))
    return angle, True


def main():
    ap = argparse.ArgumentParser(
        description="Deskew one or all crop PNGs (chords or melody) in place.")
    ap.add_argument("inputs", nargs="+",
                    help="crop PNG(s), glob(s), or a directory "
                         "(e.g. data/chords/crops or data/melody/crops)")
    ap.add_argument("--max-deg", type=float, default=6.0,
                    help="widest rotation searched, +/- deg (default 6.0)")
    ap.add_argument("--step", type=float, default=0.1,
                    help="rotation search step in deg (default 0.1)")
    ap.add_argument("--min-angle", type=float, default=0.1,
                    help="leave crops skewed less than this untouched (default 0.1)")
    ap.add_argument("--backup", action="store_true",
                    help="keep each original as <name>.orig.png")
    ap.add_argument("--dry-run", action="store_true",
                    help="report estimated angles, write nothing")
    args = ap.parse_args()

    files = collect_inputs(args.inputs)
    if not files:
        sys.exit("no PNGs matched")
    mode = "DRY-RUN, writing nothing" if args.dry_run else "rewriting in place"
    print(f"{len(files)} crop(s) to process ({mode})")

    rewritten = 0
    for i, src in enumerate(files, 1):
        name = os.path.splitext(os.path.basename(src))[0]
        angle, changed = process(src, args.max_deg, args.step, args.min_angle,
                                 args.backup, args.dry_run)
        rewritten += changed
        if args.dry_run:
            tag = "would rewrite" if abs(angle) >= args.min_angle else "level        "
        else:
            tag = "rewrote" if changed else "level  "
        print(f"[{i:4d}/{len(files)}] {tag} {angle:+.2f}deg  {name}", flush=True)

    verb = "would rewrite" if args.dry_run else "rewrote"
    print(f"done: {verb} {rewritten}/{len(files)}")


if __name__ == "__main__":
    main()
