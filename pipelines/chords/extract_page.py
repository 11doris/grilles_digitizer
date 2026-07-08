#!/usr/bin/env python3
"""
extract_page.py  -  Extract one whole page of a scanned PDF as a PNG
====================================================================

Companion to crop_tunes.py: saves the FULL page at the same resolution and
polarity as the per-tune crops (the embedded 1-bit scan at native resolution,
falling back to rasterizing at 300 dpi), so the page image lines up
pixel-for-pixel with the manifest's y0/y1/x0/x1 crop coordinates.

The page is given as the PRINTED page number, i.e. the number used in the crop
filenames (100_01_DINAH.png -> page 100). --start-page is the printed number
of the PDF's first page (same meaning as in crop_tunes.py; default 7 for
AGJ.pdf). Use --pdf-page to pass a 1-based PDF page index instead.

USAGE
  python extract_page.py AGJ.pdf 100               # printed page 100
  python extract_page.py AGJ.pdf 100 --out crops/  # write crops/100.png
  python extract_page.py AGJ.pdf 1 --pdf-page      # first page of the PDF
  python extract_page.py page341.pdf 340 --start-page 340
"""
import argparse
import os
import sys

import cv2

from crop_tunes import count_pages, extract_page, to_ink


def main():
    ap = argparse.ArgumentParser(
        description="Extract one whole PDF page as a PNG at crop resolution.")
    ap.add_argument("pdf", help="input PDF")
    ap.add_argument("page", type=int,
                    help="printed page number (as in the crop filenames), "
                         "or a 1-based PDF page with --pdf-page")
    ap.add_argument("--start-page", type=int, default=7,
                    help="printed number of the PDF's first page (default 7, "
                         "matching crop_tunes.py usage for AGJ.pdf)")
    ap.add_argument("--pdf-page", action="store_true",
                    help="treat PAGE as the 1-based page within the PDF file "
                         "instead of a printed page number")
    ap.add_argument("--out", default=".",
                    help="output directory or full .png path (default: cwd)")
    args = ap.parse_args()

    if args.pdf_page:
        pidx = args.page - 1
        printed = args.start_page + pidx
    else:
        printed = args.page
        pidx = args.page - args.start_page
    npages = count_pages(args.pdf)
    if pidx < 0 or (npages is not None and pidx >= npages):
        sys.exit(f"page {args.page} is outside {args.pdf} "
                 f"({npages} pages, printed {args.start_page}.."
                 f"{args.start_page + (npages or 1) - 1})")

    img = extract_page(args.pdf, pidx)
    if img is None:
        sys.exit(f"could not extract page {args.page} from {args.pdf}")
    gray, _ = to_ink(img)  # same polarity fix the crops get

    out = args.out
    if not out.lower().endswith(".png"):
        os.makedirs(out, exist_ok=True)
        out = os.path.join(out, f"{printed}.png")
    if not cv2.imwrite(out, gray):
        sys.exit(f"could not write {out}")
    h, w = gray.shape[:2]
    print(f"saved {out}  ({w}x{h}px, PDF page {pidx + 1}, printed page {printed})")


if __name__ == "__main__":
    main()
