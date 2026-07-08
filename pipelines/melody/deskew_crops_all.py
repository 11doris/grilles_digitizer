"""One-off: deskew every data/melody/crops PNG in place using melody_cropper's tools.

For each `<name>.png` in data/melody/crops/ (skipping the `_orig` backups), copies
the original to `<name>_orig.png` first, then estimates + corrects skew and
overwrites the crop. Does NOT commit anything.

Usage: python pipelines/melody/deskew_crops_all.py
"""
import glob
import os
import shutil
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
sys.path.insert(0, ROOT)

from pipelines.chords.crop_tunes import to_ink, write_png_1bit
from pipelines.melody.melody_cropper import deskew, estimate_skew

CROPS = os.path.join(ROOT, "data", "melody", "crops")


def process(src):
    name = os.path.splitext(os.path.basename(src))[0]
    backup = os.path.join(CROPS, f"{name}_orig.png")
    shutil.copy2(src, backup)

    native = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
    _, ink = to_ink(native)
    # Wider search range than the page pass (+/-4 deg), finer step for a single image.
    angle = estimate_skew(ink, max_deg=6.0, step=0.1)
    if abs(angle) < 0.1:
        return angle, False
    # NOTE: crops are now stored 1-bit (write_png_1bit). Rotating an already-
    # binarized crop degrades staff lines — if a crop needs re-deskewing, rerun
    # melody_cropper from the PDF instead of running this script a second time.
    write_png_1bit(src, deskew(native, angle))
    return angle, True


def main():
    pngs = sorted(
        p for p in glob.glob(os.path.join(CROPS, "*.png"))
        if not os.path.splitext(os.path.basename(p))[0].endswith("_orig")
    )
    print(f"{len(pngs)} crops to process")
    rewritten = 0
    for i, src in enumerate(pngs, 1):
        name = os.path.splitext(os.path.basename(src))[0]
        angle, changed = process(src)
        rewritten += changed
        tag = "rewrote" if changed else "level  "
        print(f"[{i:4d}/{len(pngs)}] {tag} {angle:+.2f}deg  {name}", flush=True)
    print(f"done: {rewritten}/{len(pngs)} rewritten")


if __name__ == "__main__":
    main()
