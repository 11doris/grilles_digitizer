"""One-off: deskew a single data/melody/crops PNG in place using melody_cropper's tools.

Usage: python pipelines/melody/deskew_crop.py <crop_name_without_extension>
Example: python pipelines/melody/deskew_crop.py 247_02_FLYING_HOME

Backs up the original alongside the crop as <name>.orig.png before overwriting.
"""
import os
import shutil
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root

from pipelines.chords.crop_tunes import to_ink
from pipelines.melody.melody_cropper import deskew, estimate_skew, write_png

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
CROPS = os.path.join(ROOT, "data", "melody", "crops")


def main(name):
    src = os.path.join(CROPS, f"{name}.png")
    if not os.path.exists(src):
        sys.exit(f"no such crop: {src}")

    shutil.copy2(src, os.path.join(CROPS, f"{name}.orig.png"))

    native = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
    _, ink = to_ink(native)
    # Wider search range than the page pass (+/-4 deg), finer step for a single image.
    angle = estimate_skew(ink, max_deg=6.0, step=0.1)
    print(f"estimated skew: {angle:+.2f} deg")
    if abs(angle) < 0.1:
        print("already level, nothing written")
    else:
        write_png(src, deskew(native, angle))
        print(f"rewrote {src}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python pipelines/melody/deskew_crop.py <crop_name_without_extension>")
    main(sys.argv[1])
