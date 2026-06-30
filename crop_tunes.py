#!/usr/bin/env python3
"""
crop_tunes.py  -  Crop individual jazz tunes from scanned chord-grid pages
=========================================================================

Source material: "Anthologie des grilles de jazz" style pages where several
tunes are stacked vertically. Each tune has a large hand-lettered TITLE in
capitals (NOT inside a box), a small genre/tempo sub-header, then a grid of
chord boxes, and a per-tune column of discography references on the right.

The scans are 1-bit (bilevel), ~235 ppi, with no higher-quality source.

WHAT IS RELIABLE (done automatically)
  * Splitting a page into tunes and naming them, GIVEN the book index (--index).
    The script is "index-driven": the index lists exactly which titles are on
    each printed page, so the image is only used to LOCATE each known title.
    It detects candidate title bands, then aligns the page's known titles to
    them in order. Benefits:
      - a title whose hand-lettering won't OCR is still placed by its position
        in the known sequence (e.g. between its neighbours);
      - chord rows misread as titles align to nothing and are ignored;
      - tunes with several grid blocks are not over-split;
      - the exact spelling comes from the index.
    Detection is resolution-independent (every page is normalised to a working
    width internally; crops are still saved at native resolution).

  * Without --index the script falls back to pure geometry + fuzzy OCR, which is
    much less reliable on this hand-lettering. Supplying the index is strongly
    recommended.

WHAT STILL NEEDS A GLANCE
  * Each placed title carries a confidence; low ones are flagged review=yes in
    the manifest. Titles placed purely by sequence position (OCR unreadable)
    show low confidence even when correct, so a quick scan of flagged rows is
    worthwhile. The printed PAGE NUMBER is supplied via --start-page (the index
    is keyed on it), not read from the scan.

USAGE
  Step 1  detect + crop + match titles to the index:
      python crop_tunes.py page341_Riverboat.pdf \
             --out ./tunes --start-page 340 --no-sidebar \
             --index AGJ_index.pdf

  Step 2  open ./tunes/manifest.csv, check the rows with review=yes (fix the
          'title' column only if wrong), then apply the final filenames:
      python crop_tunes.py --apply ./tunes/manifest.csv

  Process the whole book by giving each PDF its correct --start-page into the
  same --out folder; the manifest accumulates across runs.

RESUMABLE / RE-ENTRANT
  Progress is checkpointed to manifest.csv after every page (atomic write), and
  pages are extracted one at a time. If the run is cancelled or killed, just run
  the SAME command again: pages whose crops already exist are skipped and it
  continues from where it stopped. Delete a crop (or the manifest) to force the
  affected page(s) to be redone.

OPTIONS
  --index PATH       book index PDF/.txt; titles are matched to it (recommended)
  --page-window N    also match against +/-N neighbouring index pages (use 1 if
                     printed page numbers might be off by one). Default 0.
  --review-below F   flag titles with match confidence below F (default 0.55)
  --out DIR          output directory (default ./tunes)
  --start-page V     printed number of the FIRST page of each PDF ('auto' =
                     digits from the filename). Incremented per page.
  --format png|pdf   output format for crops (default png)
  --no-sidebar       crop to the chord-grid only, excluding the right-hand
                     discography column (default: include it).
  --full-width       no horizontal cropping at all: keep the full page width
                     (discography column + both margins). Vertical cuts only.
  --keep-crossref    also crop trailing cross-reference lines that have a big
                     title but no chord grid (e.g. "EASY TO REMEMBER -> ...").
                     Default: skip them.
  --pad PX           padding added around each crop (default 12)
  --scale F          rescale output crops by factor F (default 1.0 = native).
  --debug            also write *_debug.png overlays showing detected regions.

MANIFEST COLUMNS
  title      final title used for the filename (from the index when matched)
  conf       match confidence 0..~1 (higher = surer)
  review     'yes' when conf is low and the title is worth a human glance
  ocr_raw    what the title OCR actually read (for debugging)
  alt_title  full index entry incl. any (parenthetical) alternate title
"""
import argparse, csv, os, re, sys, subprocess, tempfile, time, shutil

_START = time.time()


def log(msg=""):
    """Print a timestamped status line immediately (unbuffered)."""
    print(f"[{time.time() - _START:6.1f}s] {msg}", flush=True)
import numpy as np
import cv2

try:
    import pytesseract
    HAVE_TESS = True
except Exception:
    HAVE_TESS = False


# ----------------------------------------------------------------------------
# Image loading: prefer the embedded bilevel stencil (no resampling); fall back
# to rendering the PDF page at high DPI.
# ----------------------------------------------------------------------------
def count_pages(pdf_path):
    """Return the number of pages in a PDF, or None if it can't be determined."""
    try:
        info = subprocess.run(["pdfinfo", pdf_path], check=True,
                              capture_output=True, text=True).stdout
        return int(re.search(r"Pages:\s+(\d+)", info).group(1))
    except Exception:
        return None


def extract_page(pdf_path, pidx, dpi=300):
    """Extract ONE page (0-based pidx) as a grayscale image.

    Extracts just that page (not the whole PDF) so progress is visible and a
    cancelled run can resume without redoing pages. Prefers the embedded 1-bit
    scan; falls back to rasterizing the single page.
    """
    p1 = pidx + 1  # poppler is 1-based
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "img")
        try:
            subprocess.run(["pdfimages", "-f", str(p1), "-l", str(p1), "-png",
                            pdf_path, base], check=True, capture_output=True)
            files = sorted(f for f in os.listdir(td) if f.endswith(".png"))
        except Exception:
            files = []
        if files:
            # If a page has several embedded images, the scan is the largest.
            f = max(files, key=lambda f: os.path.getsize(os.path.join(td, f)))
            img = cv2.imread(os.path.join(td, f), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                return img
        # Fallback: rasterize just this page.
        base2 = os.path.join(td, "pg")
        try:
            subprocess.run(["pdftoppm", "-f", str(p1), "-l", str(p1), "-r",
                            str(dpi), "-png", pdf_path, base2],
                           check=True, capture_output=True)
            pg = sorted(f for f in os.listdir(td) if f.startswith("pg"))
            if pg:
                return cv2.imread(os.path.join(td, pg[0]), cv2.IMREAD_GRAYSCALE)
        except Exception:
            pass
    return None


def pdf_page_images(pdf_path, dpi=300):
    """Yield (page_index_0based, gray_uint8_image) for each page of a PDF."""
    # Try to pull embedded images first (best fidelity for 1-bit scans).
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "img")
        try:
            log(f"    extracting embedded images (pdfimages)...")
            subprocess.run(["pdfimages", "-png", pdf_path, base],
                           check=True, capture_output=True)
            files = sorted(f for f in os.listdir(td) if f.endswith(".png"))
        except Exception:
            files = []
        # Heuristic: one embedded image per page in these scans.
        # Confirm page count.
        try:
            info = subprocess.run(["pdfinfo", pdf_path],
                                  check=True, capture_output=True, text=True).stdout
            npages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
        except Exception:
            npages = len(files)

        if len(files) == npages and npages > 0:
            log(f"    using {len(files)} embedded page image(s) at native resolution")
            for i, f in enumerate(files):
                img = cv2.imread(os.path.join(td, f), cv2.IMREAD_GRAYSCALE)
                yield i, img
            return
        else:
            log(f"    embedded-image count ({len(files)}) != pages ({npages}); "
                f"rasterizing at {dpi} dpi instead")

    # Fallback: rasterize each page.
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "pg")
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf_path, base],
                       check=True, capture_output=True)
        for i, f in enumerate(sorted(os.listdir(td))):
            img = cv2.imread(os.path.join(td, f), cv2.IMREAD_GRAYSCALE)
            yield i, img


def to_ink(gray):
    """Return (gray_black_on_white, ink_mask) with ink=255.
    Handles the inverted-stencil polarity (white ink on black)."""
    g = gray.copy()
    if g.mean() < 110:            # mostly dark -> stencil is inverted
        g = 255 - g
    _, ink = cv2.threshold(g, 128, 255, cv2.THRESH_BINARY_INV)
    return g, ink


def _open(mask, kx, ky):
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)))


# --- chord-grid block detection (calibrated for the working width) ----------
# The 1-bit scans frequently have broken/dotted grid rules, so a long horizontal
# opening kernel (the old 150 px) erased whole rows and the grid went undetected
# -- which in turn produced bogus title bands and badly truncated crops. A
# shorter kernel + lower row threshold tolerates the breaks and recovers one
# clean block per tune. The vertical kernel stays long so hand-lettered TITLE
# strokes (~55-75 px) are never mistaken for grid rules.
_GRID_HK = 90        # min horizontal grid-rule length (px) for block detection
_GRID_VK = 85        # min vertical grid-rule length (px)
_GRID_THR = 0.06     # fraction of content width showing grid structure in a row
_GRID_CLOSE = 45     # bridge vertical gaps (px) between grid rows of one block


def grid_row_mask(ink, x0, x1):
    """Boolean per-row mask: True where a chord grid (long H and V rules
    coexisting) is present. Shared by the index-driven and geometry paths so
    both see the same blocks."""
    H = ink.shape[0]
    cw = max(x1 - x0, 1)
    hl = _open(ink, _GRID_HK, 1)
    vl = _open(ink, 1, _GRID_VK)
    Hd = cv2.dilate(hl, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 70)))
    Vd = cv2.dilate(vl, cv2.getStructuringElement(cv2.MORPH_RECT, (70, 1)))
    grid = cv2.bitwise_and(Hd, Vd)
    prof = (grid[:, x0:x1] > 0).sum(1).astype(float) / cw
    isg = (prof > _GRID_THR).astype(np.uint8).reshape(-1, 1)
    isg = cv2.morphologyEx(
        isg, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, _GRID_CLOSE))).ravel() > 0
    return isg


def grid_blocks_from_mask(isg, H, minh=60):
    """Contiguous True runs of `isg` longer than `minh` -> [(top, bot), ...]."""
    runs, cur, s = [], isg[0], 0
    for y in range(1, H):
        if isg[y] != cur:
            runs.append((cur, s, y)); cur, s = isg[y], y
    runs.append((cur, s, H))
    return [(s, e) for v, s, e in runs if v and e - s > minh]


def content_x_bounds(ink):
    col = (ink > 0).sum(0)
    thr = col.max() * 0.02
    xs = np.where(col > thr)[0]
    return int(xs.min()), int(xs.max())


def grid_right_edge(ink, x0, x1):
    """Rightmost strong vertical line = right edge of the chord grid
    (used to exclude the discography sidebar)."""
    vl = _open(ink, 1, 85)
    col = (vl[:, x0:x1] > 0).sum(0)
    strong = np.where(col > col.max() * 0.25)[0]
    return x0 + int(strong.max()) if len(strong) else x1


def _tallest_run(bool_rows):
    best = cur = cs = bests = 0
    for i, v in enumerate(bool_rows):
        if v:
            if cur == 0:
                cs = i
            cur += 1
            if cur > best:
                best, bests = cur, cs
        else:
            cur = 0
    return bests, best


# ----------------------------------------------------------------------------
# Core: find tune boundaries on one page.
# ----------------------------------------------------------------------------
def detect_tunes(gray, keep_crossref=False):
    g, ink = to_ink(gray)
    H, W = ink.shape
    x0, x1 = content_x_bounds(ink)
    cw = x1 - x0

    # --- box grid = regions where long horizontal AND vertical lines coexist.
    blocks = grid_blocks_from_mask(grid_row_mask(ink, x0, x1), H)
    if not blocks:
        return g, (x0, x1), H, []

    # --- de-lined text image (vertical kernel 80 keeps title strokes intact).
    hl = _open(ink, 150, 1)
    vlT = _open(ink, 1, 80)
    txt = cv2.subtract(ink, cv2.bitwise_or(hl, vlT))
    txt = _open(txt, 2, 2)
    leftw = int(cw * 0.46)

    def title_in(gs, ge, hmin=46):
        """Tallest single title-like band in a gap (used for the trailing
        cross-reference test)."""
        bands = title_bands_in(gs, ge, hmin)
        return max(bands, key=lambda r: r[1]) if bands else None

    def title_bands_in(gs, ge, hmin=46):
        """ALL title-like text bands in a gap, top to bottom.

        A gap can hold more than one title when a tune has no grid of its own
        (its title sits directly above the next tune's title). Returns a list of
        (abs_top, height). Bands that fill their whole gap (chord lettering
        bleeding from an adjacent grid block) are rejected.
        """
        a, b = max(0, gs - 6), min(H, ge + 8)
        band_h = b - a
        if band_h < 40:
            return []
        left = txt[a:b, x0:x0 + leftw]
        rowink = (left > 0).sum(1) > 4
        # bridge tiny vertical gaps inside one title's lettering
        rr = cv2.morphologyEx(rowink.astype(np.uint8).reshape(-1, 1),
                              cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (1, 14))).ravel() > 0
        out, i, nrows = [], 0, len(rr)
        while i < nrows:
            if not rr[i]:
                i += 1
                continue
            j = i
            while j < nrows and rr[j]:
                j += 1
            h = j - i
            if h >= hmin and h <= 0.9 * band_h:
                region = left[i:j]
                width = int(((region > 0).sum(0) > 0).sum())
                if width >= 120:
                    out.append((a + i, h))
            i = j
        return out

    gaps = [(0, blocks[0][0])] + \
           [(blocks[i][1], blocks[i + 1][0]) for i in range(len(blocks) - 1)]
    titles = [r for gs, ge in gaps if (r := title_in(gs, ge))]

    # Top-of-page tune whose title is clipped by the scan: if the first grid
    # block has no detected title above it, start a tune at the page top.
    if not titles or titles[0][0] > blocks[0][0]:
        titles.insert(0, (0, 0))
    titles.sort()

    # Trailing big-title line with no grid after it = cross-reference.
    tail = title_in(blocks[-1][1], H, hmin=34)
    if tail and keep_crossref:
        titles.append(tail)
        titles.sort()
        last_bottom = H
    else:
        last_bottom = (tail[0] - 10) if tail else min(H, blocks[-1][1] + 35)

    tunes = []
    for i, (top, rl) in enumerate(titles):
        bot = titles[i + 1][0] - 10 if i + 1 < len(titles) else last_bottom
        tunes.append((max(0, top - 10), bot, rl))
    return g, (x0, x1), H, tunes


# ----------------------------------------------------------------------------
# Title OCR (best effort) + filename helpers.
# ----------------------------------------------------------------------------
def ocr_title(gray, top, rl, x0, x1):
    """Best-effort OCR of the hand-lettered title.

    The title is the *largest* lettering in its band; small typeset text
    (composer credit, genre/tempo labels, discography) is removed by keeping
    only tall connected components before recognition. Output is intended for
    fuzzy matching against the index, so spacing/punctuation are not relied on.
    """
    if not HAVE_TESS:
        return ""
    g, ink = to_ink(gray)
    hl = _open(ink, 150, 1)
    vlT = _open(ink, 1, 80)
    txt = cv2.subtract(ink, cv2.bitwise_or(hl, vlT))
    H = ink.shape[0]
    band_h = max(rl, 60)
    band = txt[max(0, top - 4):min(H, top + band_h + 22), x0:x1]
    if band.size == 0:
        return ""
    n, lab, stats, _ = cv2.connectedComponentsWithStats((band > 0).astype(np.uint8), 8)
    if n <= 1:
        return ""
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    hmax = int(heights.max())
    thr = max(30, int(0.55 * hmax))          # title glyphs are the tallest things
    keep = np.zeros_like(band)
    for i in range(1, n):
        h = stats[i, cv2.CC_STAT_HEIGHT]
        w = stats[i, cv2.CC_STAT_WIDTH]
        a = stats[i, cv2.CC_STAT_AREA]
        if thr <= h <= band.shape[0] and w < band.shape[1] * 0.8 and a > 40:
            keep[lab == i] = 255
    cols = np.where(keep.any(axis=0))[0]
    if not len(cols):
        return ""
    keep = keep[:, max(0, cols[0] - 6):cols[-1] + 6]
    up = cv2.resize(keep, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    up = cv2.GaussianBlur(up, (0, 0), 1.0)
    up = 255 - cv2.copyMakeBorder(up, 18, 18, 18, 18, cv2.BORDER_CONSTANT, value=0)
    cfg = "--oem 1 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    raw = pytesseract.image_to_string(up, config=cfg).strip()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z ]", " ", raw)).strip()


# ----------------------------------------------------------------------------
# Title index (from the book's alphabetical index PDF) + fuzzy matching
# ----------------------------------------------------------------------------
from difflib import SequenceMatcher

_LEAD = re.compile(r'^(.+?)\s*\.{2,}\s*(\d{1,3})\s*$')        # TITLE .... 123
_GLUE = re.compile(r"^(.+?[A-Za-z\)\'\?!])(\d{1,3})\s*$")     # TITLE123 (no dots)
_ONLYNUM = re.compile(r'^\d{1,3}$')


def _index_columns(lines):
    """Detect the x (character-offset) where each printed index column starts.

    The index is typeset in several alphabetical columns per page; `pdftotext
    -layout` preserves them as fixed character offsets. We cluster the start
    offset of every text cell and keep the (up to 3) most common, well-separated
    peaks. Returns the sorted list of column-start offsets (e.g. [0, 98, 195])."""
    from collections import Counter
    cnt = Counter()
    for ln in lines:
        for m in re.finditer(r"\S.*?(?=\s{2,}|$)", ln):
            cnt[m.start()] += 1
    cols = []
    for off, _ in sorted(cnt.items(), key=lambda x: -x[1]):
        if all(abs(off - c) > 30 for c in cols):
            cols.append(off)
        if len(cols) >= 3:
            break
    return sorted(cols) or [0]


def load_index(path):
    """Parse the book's index PDF/text into {page:int -> [full titles]}.

    Accepts the index PDF (rendered with `pdftotext -layout`) or a pre-extracted
    .txt file. Returns {} on any failure so detection can proceed without it.

    The index lists titles in SEVERAL columns, read top-to-bottom within a column
    and then column-by-column (alphabetical order). We therefore (1) detect the
    column offsets, (2) bucket every line's cells into their column, and (3) walk
    each column's stream in order so each page's titles come out in the same
    top-to-bottom order they appear on the scanned music page. Parsing per column
    also stops one entry's dotted leader from swallowing the next column's title
    when the inter-column gap is only a couple of spaces.
    """
    try:
        if path.lower().endswith((".txt",)):
            text = open(path, encoding="utf-8", errors="replace").read()
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name
            # -layout keeps the columns at fixed character offsets so we can
            # separate them; without it pdftotext reflows columns into one line.
            subprocess.run(["pdftotext", "-layout", path, tmp], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            text = open(tmp, encoding="utf-8", errors="replace").read()
            os.unlink(tmp)
    except Exception as e:
        print(f"!! could not read index {path}: {e}", file=sys.stderr)
        return {}

    lines = text.splitlines()
    cols = _index_columns(lines)

    def col_of(start):
        return min(range(len(cols)), key=lambda i: abs(start - cols[i]))

    # Split every line into its columns; a blank line is a reset marker so a
    # wrapped-title buffer never bridges two physical index pages.
    streams = [[] for _ in cols]
    for ln in lines:
        if not ln.strip():
            for st in streams:
                st.append(None)
            continue
        for m in re.finditer(r"\S.*?(?=\s{2,}|$)", ln):
            streams[col_of(m.start())].append(m.group().strip())

    by_page = {}
    for st in streams:
        buf = ""
        for cell in st:
            if cell is None:                  # blank line -> drop pending wrap
                buf = ""; continue
            if _ONLYNUM.match(cell):          # index's own page-header digits
                buf = ""; continue
            cand = (buf + " " + cell).strip() if buf else cell
            m = _LEAD.match(cand) or _GLUE.match(cand)
            if m:
                title = re.sub(r'\s+', ' ', m.group(1)).strip(" .")
                # Drop the book-title running header if it got glued to an entry.
                title = re.sub(r'^ANTHOLOGIE DES GRILLES DE JAZZ\s+', '', title).strip()
                if title:
                    by_page.setdefault(int(m.group(2)), []).append(title)
                buf = ""
            else:
                buf = cand                    # wrapped (long) title, keep building
    return by_page


def _squash(s):
    return re.sub(r"[^A-Z]", "", s.upper())


def _title_score(ocr, cand):
    a, b = _squash(ocr), _squash(cand)
    if not a or not b:
        return 0.0
    base = SequenceMatcher(None, a, b).ratio()
    L = min(len(a), len(b))                   # credit OCR that caught only the start
    pref = SequenceMatcher(None, a[:L], b[:L]).ratio() if L >= 4 else 0.0
    return max(base, 0.5 * base + 0.5 * pref)


def match_titles(ocr_list, candidates):
    """Assign each detected tune (in reading order) to a distinct index title.

    Returns list of (canonical_full_title, confidence). Uses fuzzy score plus a
    small reading-order/alphabetical-order prior to break ties (e.g. clipped or
    unreadable titles resolved by elimination). Falls back to the raw OCR string
    with confidence 0 when there are no candidates.
    """
    n = len(ocr_list)
    if not candidates:
        return [(t, 0.0) for t in ocr_list]
    pairs = []
    for i, ocr in enumerate(ocr_list):
        for j, c in enumerate(candidates):
            s = _title_score(ocr, c)
            rt = i / max(1, n - 1)
            rc = j / max(1, len(candidates) - 1)
            s += 0.08 * (1 - abs(rt - rc))    # tie-breaking prior only
            pairs.append((s, i, j))
    pairs.sort(reverse=True)
    res, usedc = {}, set()
    for s, i, j in pairs:
        if i in res or j in usedc:
            continue
        res[i] = (candidates[j], round(float(s), 3))
        usedc.add(j)
    return [res.get(i, (ocr_list[i], 0.0)) for i in range(n)]


def main_title(full):
    """The part used for the filename: drop a trailing parenthetical alt-title."""
    return re.split(r'\s*\(', full, 1)[0].strip() or full


# ----------------------------------------------------------------------------
# INDEX-DRIVEN detection
# ----------------------------------------------------------------------------
# The index lists exactly which titles are on a page, in order. We detect
# candidate title bands generously, then align the known titles to those bands
# (order-preserving). Extra bands (chord rows) align to nothing; a title whose
# OCR failed is still placed by its position in the known sequence. This is far
# more robust than letting page geometry decide how many tunes exist.
# ----------------------------------------------------------------------------
def _grid_blocks(ink, x0, x1, H):
    blocks = grid_blocks_from_mask(grid_row_mask(ink, x0, x1), H)
    hl = _open(ink, 150, 1)        # long-rule mask for de-lining the text image
    return blocks, hl


def _title_bands(txt, x0, leftw, a, b, hmin=40):
    """All title-like text bands within rows [a,b], top to bottom.

    Detect generously: in the index-driven pipeline, false bands (chord rows)
    are discarded by the title alignment, so geometry need not be strict. We
    only require a band to be tall enough and wide enough to be lettering.
    """
    if b - a < 30:
        return []
    left = txt[a:b, x0:x0 + leftw]
    rowink = (left > 0).sum(1) > 4
    rr = cv2.morphologyEx(rowink.astype(np.uint8).reshape(-1, 1), cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (1, 16))).ravel() > 0
    out, i, nrows = [], 0, len(rr)
    while i < nrows:
        if not rr[i]:
            i += 1; continue
        j = i
        while j < nrows and rr[j]:
            j += 1
        h = j - i
        if h >= hmin:
            region = left[i:j]
            width = int(((region > 0).sum(0) > 0).sum())
            if width >= 100:
                out.append((a + i, h))
        i = j
    return out


def candidate_bands(gray):
    """Return (g, (x0,x1), H, blocks, bands). `bands` = candidate title bands
    (top, height) over every non-grid region, top to bottom."""
    g, ink = to_ink(gray)
    H, W = ink.shape
    x0, x1 = content_x_bounds(ink)
    cw = x1 - x0
    blocks, hl = _grid_blocks(ink, x0, x1, H)
    vlT = _open(ink, 1, 80)
    txt = cv2.subtract(ink, cv2.bitwise_or(hl, vlT))
    txt = _open(txt, 2, 2)
    leftw = int(cw * 0.46)
    if blocks:
        regions = [(0, blocks[0][0])]
        regions += [(blocks[i][1], blocks[i + 1][0]) for i in range(len(blocks) - 1)]
        regions.append((blocks[-1][1], H))          # trailing region (cross-refs)
    else:
        regions = [(0, H)]
    bands = []
    for a, b in regions:
        bands.extend(_title_bands(txt, x0, leftw, a, b))
    bands.sort()
    # top-of-page tune whose title is clipped off by the scan
    if blocks and (not bands or bands[0][0] > blocks[0][0] + 5):
        bands.insert(0, (0, 0))
    return g, (x0, x1), H, blocks, bands


def align_titles_to_bands(titles, band_texts, match_bias=0.05, band_bonus=None):
    """Order-preserving (Needleman-Wunsch) alignment of known `titles` to the
    OCR of detected `band_texts`. Returns list of (title_idx, band_idx, score).
    Every matched pair adds its similarity plus a small bias, so as many known
    titles as possible are placed (in order) even when OCR is weak; spurious
    bands and truly-absent titles are skipped.

    `band_bonus[j]` (optional) is a small per-band reward added when band j is
    matched. It is used to favour bands that sit directly above a chord grid --
    i.e. that look like a real tune title -- so a title whose OCR came back blank
    snaps onto its grid-backed band instead of an arbitrary footer band lower on
    the page (which would let the previous tune bleed across the missing title)."""
    m, n = len(titles), len(band_texts)
    if m == 0 or n == 0:
        return []
    bonus = band_bonus or [0.0] * n
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    bk = [[None] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        bk[i][0] = ('T',)
    for j in range(1, n + 1):
        bk[0][j] = ('B',)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            s = _title_score(band_texts[j - 1], titles[i - 1])
            mscore = dp[i - 1][j - 1] + s + match_bias + bonus[j - 1]
            sT = dp[i - 1][j]            # skip title i (absent / gridless)
            sB = dp[i][j - 1]            # skip band j (chord row / noise)
            best = max(mscore, sT, sB)
            dp[i][j] = best
            bk[i][j] = ('M', i - 1, j - 1, s) if best == mscore else \
                       (('T',) if best == sT else ('B',))
    i, j, pairs = m, n, []
    while i > 0 and j > 0:
        t = bk[i][j]
        if t[0] == 'M':
            pairs.append((t[1], t[2], t[3])); i -= 1; j -= 1
        elif t[0] == 'T':
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def detect_indexed(gray, page_titles, keep_crossref=False):
    """Index-driven tune detection. `page_titles` = the index's titles for this
    printed page, in order. Returns (g, (x0,x1), H, tunes) where each tune is
    (top, bot, title, conf)."""
    g, (x0, x1), H, blocks, bands = candidate_bands(gray)
    if not bands:
        return g, (x0, x1), H, []
    band_texts = [ocr_title(gray, top, h, x0, x1) for (top, h) in bands]
    # A real tune title sits just above its chord grid; reward bands that have a
    # grid block starting shortly below them so blank-OCR titles snap there
    # rather than onto a footer band lower on the page.
    band_bonus = [0.08 if any(top < bs <= top + 220 for (bs, _be) in blocks)
                  else 0.0 for (top, _h) in bands]
    pairs = align_titles_to_bands(page_titles, band_texts, band_bonus=band_bonus)
    # matched (band_top, title, score) sorted top->bottom
    placed = sorted((bands[bj][0], page_titles[ti], sc) for ti, bj, sc in pairs)
    n = len(placed)
    tunes = []
    for idx, (top, title, sc) in enumerate(placed):
        top2 = max(0, top - 10)
        if idx + 1 < n:
            # Cut just above the NEXT title. Everything below this tune's title
            # and above the next one stays here -- in particular a small VARIANT
            # block printed below the main grid belongs to THIS tune, not the
            # next, so it must not be split off.
            bot = placed[idx + 1][0] - 10
        else:
            # Last tune on the page: extend to the bottom of its own grid (so the
            # discography footer is trimmed). If it has no grid below its title
            # -- a cross-reference, or a tune whose grid is clipped onto the next
            # printed page (e.g. a title sitting at the very foot of the page) --
            # run to the page bottom instead of cutting it to a sliver.
            own = [be for (bs, be) in blocks if bs >= top2]
            bot = min(H, max(own) + 35) if own else H
        has_grid = any(not (be <= top2 or bs >= bot) for (bs, be) in blocks)
        if not has_grid and not keep_crossref:
            continue                       # titled region with no grid = cross-ref
        tunes.append((top2, bot, title, round(float(sc), 3)))
    return g, (x0, x1), H, tunes


def slugify(title):
    t = title.upper().strip()
    t = t.replace("&", " AND ")
    t = re.sub(r"[^A-Z0-9]+", "_", t)
    return t.strip("_")


def parse_start_page(spec, pdf_path):
    if spec is not None and spec != "auto":
        return int(spec)
    m = re.search(r"(\d+)", os.path.basename(pdf_path))
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------
def cmd_detect(args):
    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "manifest.csv")

    # --- startup banner / environment check ---------------------------------
    log("=" * 60)
    log("crop_tunes: starting")
    log(f"  output dir : {os.path.abspath(args.out)}")
    log(f"  inputs     : {len(args.inputs)} PDF(s)")
    log(f"  options    : format={args.format} "
        f"{'full-width' if args.full_width else ('grid-only' if args.no_sidebar else 'with-sidebar')} "
        f"pad={args.pad} scale={args.scale} page_window={args.page_window}")
    log("  checking required tools:")
    for tool in ("pdfimages", "pdfinfo", "pdftoppm", "pdftotext"):
        log(f"    {tool:<10} {'found' if shutil.which(tool) else 'MISSING (install poppler-utils)'}")
    log(f"    {'tesseract':<10} "
        f"{'found' if (HAVE_TESS and shutil.which('tesseract')) else 'MISSING (titles will be blank)'}")

    index = load_index(args.index) if args.index else {}
    if args.index:
        log(f"  index      : {sum(len(v) for v in index.values())} titles "
            f"across {len(index)} pages loaded")
    else:
        log("  index      : none (titles will be raw OCR guesses)")

    # Pre-count pages so progress can show page X of total.
    page_counts = {pdf: count_pages(pdf) for pdf in args.inputs}
    grand_total = sum(c or 0 for c in page_counts.values())
    log(f"  total pages to process: {grand_total or 'unknown'}")
    log("=" * 60)

    fieldnames = ["source", "page", "index", "title", "conf", "review",
                  "ocr_raw", "alt_title", "y0", "y1", "x0", "x1", "current_file"]
    sources_now = {os.path.basename(p) for p in args.inputs}

    # --- resume support -----------------------------------------------------
    # The manifest is the checkpoint. Rows from OTHER output runs (different
    # source PDFs) are preserved untouched. For the PDFs in THIS run, a page is
    # considered already done if it has rows in the manifest AND every one of
    # its crop files still exists on disk; such pages are skipped (no
    # re-extraction, no re-OCR). Everything is flushed after each page, so a
    # cancelled run resumes from the next unfinished page.
    kept_other, existing_by_key = [], {}
    if os.path.exists(manifest_path):
        with open(manifest_path, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("source") in sources_now:
                    existing_by_key.setdefault((r["source"], str(r["page"])), []).append(r)
                else:
                    kept_other.append(r)
    done_keys = set()
    for key, rs in existing_by_key.items():
        if all(os.path.exists(os.path.join(args.out, r["current_file"])) for r in rs):
            done_keys.add(key)
    if done_keys:
        log(f"  resume     : {len(done_keys)} page(s) already done in "
            f"{os.path.basename(manifest_path)} will be skipped")

    def page_sort_key(r):
        try:
            pg = int(r["page"])
        except (ValueError, TypeError):
            pg = 10**9
        try:
            ix = int(r["index"])
        except (ValueError, TypeError):
            ix = 0
        return (r["source"], pg, str(r["page"]), ix)

    # Seed the in-memory manifest with everything we are keeping (other
    # sources + already-done pages of this run), then flush incrementally.
    merged = list(kept_other)
    for key in done_keys:
        merged.extend(existing_by_key[key])

    def flush_manifest():
        merged.sort(key=page_sort_key)
        tmp = manifest_path + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader(); w.writerows(merged)
        os.replace(tmp, manifest_path)            # atomic; survives a cancel

    flush_manifest()

    crops_new = 0
    pages_done = pages_skipped = 0
    seen = 0
    for fi, pdf in enumerate(args.inputs, 1):
        base = os.path.basename(pdf)
        base_page = parse_start_page(args.start_page, pdf)
        npages = page_counts.get(pdf) or 0
        log("")
        log(f"FILE {fi}/{len(args.inputs)}: {base}  "
            f"({npages or '?'} pages, "
            f"printed start page = {base_page if base_page is not None else 'auto'})")
        for pidx in range(npages):
            seen += 1
            page_no = (base_page + pidx) if base_page is not None \
                else f"{os.path.splitext(base)[0]}-p{pidx+1}"
            pct = f"{100*seen/grand_total:4.0f}%" if grand_total else "  ? "
            head = f"  [{pct} | {seen}/{grand_total or '?'}] {base} page {pidx+1}/{npages}  printed# {page_no}"

            if (base, str(page_no)) in done_keys:
                pages_skipped += 1
                log(f"{head}  -- already done, skipping")
                continue

            log(f"{head}  -- extracting image...")
            native = extract_page(pdf, pidx)
            if native is None:
                log(f"        !! could not read page image -- left for a later run")
                continue
            Hn, Wn = native.shape[:2]

            # --- resolution normalization -------------------------------------
            # All detection thresholds are calibrated for a canonical width.
            # Resample the page to that width for detection + OCR, then map the
            # resulting boundaries back to native pixels so the saved crops keep
            # full resolution. This makes the script work at ANY input scale.
            cw_target = args.work_width
            sf = cw_target / Wn
            if abs(sf - 1.0) < 0.02:
                work = native
            else:
                interp = cv2.INTER_AREA if sf < 1 else cv2.INTER_CUBIC
                work = cv2.resize(native, (cw_target, max(1, round(Hn * sf))),
                                  interpolation=interp)
            log(f"        native {Wn}x{Hn} -> work {work.shape[1]}x{work.shape[0]} "
                f"(scale {sf:.3f}); detecting tunes...")

            g_native, _ = to_ink(native)          # crop from full-res, correct polarity
            page_titles = index.get(page_no, []) if isinstance(page_no, int) else []

            if page_titles:
                # INDEX-DRIVEN: place the page's known titles onto the image.
                gw, (x0w, x1w), Hw, idx_tunes = detect_indexed(
                    work, page_titles, args.keep_crossref)
                tunes_final = [(top, bot, full, conf,
                                ocr_title(work, top, max(0, min(bot - top, 90)), x0w, x1w))
                               for (top, bot, full, conf) in idx_tunes]
                log(f"        index lists {len(page_titles)} title(s); "
                    f"placed {len(tunes_final)} tune(s) on the page")
            else:
                # FALLBACK (page not in index): geometry + fuzzy match.
                gw, (x0w, x1w), Hw, geo = detect_tunes(work, args.keep_crossref)
                ocrs = [ocr_title(work, top, rl, x0w, x1w) for (top, bot, rl) in geo]
                tunes_final = [(top, bot, oc, 0.0, oc)
                               for (top, bot, rl), oc in zip(geo, ocrs)]
                log(f"        no index for this page; geometry found "
                    f"{len(tunes_final)} tune(s)")

            sx = Wn / gw.shape[1]                 # work -> native scale factors
            sy = Hn / gw.shape[0]
            right_w = x1w if not args.no_sidebar else grid_right_edge(to_ink(work)[1], x0w, x1w) + 8
            if args.debug:
                vis = cv2.cvtColor(gw, cv2.COLOR_GRAY2BGR)

            page_rows = []
            for ti, (top, bot, full, conf, ocr_raw) in enumerate(tunes_final, 1):
                title = main_title(full) if page_titles else full
                review = "yes" if (conf < args.review_below or not _squash(title)) else ""
                pad = args.pad
                # map work-space boundaries to native pixels
                top_n, bot_n = int(top * sy), int(bot * sy)
                y0c, y1c = max(0, top_n - pad), min(Hn, bot_n + pad)
                if args.full_width:
                    x0c, x1c = 0, Wn                  # no horizontal cropping
                else:
                    x0c = max(0, int(x0w * sx) - pad)
                    x1c = min(Wn, int(right_w * sx) + pad)
                crop = g_native[y0c:y1c, x0c:x1c]
                if args.scale != 1.0:
                    crop = cv2.resize(crop, None, fx=args.scale, fy=args.scale,
                                      interpolation=cv2.INTER_CUBIC)
                fn = f"{page_no}_{ti:02d}_{slugify(title) or 'UNTITLED'}." + args.format
                outp = os.path.join(args.out, fn)
                if args.format == "pdf":
                    _save_pdf(crop, outp)
                else:
                    cv2.imwrite(outp, crop)
                crops_new += 1
                page_rows.append(dict(source=base, page=page_no, index=ti,
                                 title=title, conf=f"{conf:.2f}", review=review,
                                 ocr_raw=ocr_raw, alt_title=full, y0=y0c, y1=y1c,
                                 x0=x0c, x1=x1c, current_file=fn))
                if args.debug:
                    # overlay drawn in WORK space
                    xx0 = 0 if args.full_width else max(0, int(x0w) - pad)
                    xx1 = gw.shape[1] if args.full_width else min(gw.shape[1], int(right_w) + pad)
                    cv2.rectangle(vis, (xx0, max(0, top - pad)),
                                  (xx1, min(Hw, bot + pad)), (0, 0, 255), 4)
                    cv2.putText(vis, (title or "?")[:24], (xx0 + 8, max(0, top - pad) + 34),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 120, 255), 3)
                flag = "  <-- REVIEW" if review else ""
                log(f"        tune {ti}: conf {conf:.2f}  ocr={ocr_raw[:24]!r} "
                    f"-> {title!r}  [{x1c-x0c}x{y1c-y0c}px] saved {fn}{flag}")
            if args.debug:
                dp = os.path.join(args.out, f"{page_no}_debug.png")
                s = 800 / gw.shape[1]
                cv2.imwrite(dp, cv2.resize(vis, (800, int(gw.shape[0] * s))))
                log(f"        wrote debug overlay {os.path.basename(dp)}")

            # Checkpoint: drop any stale rows for this page, add fresh, flush.
            merged[:] = [r for r in merged
                         if (r["source"], str(r["page"])) != (base, str(page_no))]
            merged.extend(page_rows)
            done_keys.add((base, str(page_no)))
            pages_done += 1
            flush_manifest()

    nrev = sum(1 for r in merged if r.get("source") in sources_now and r.get("review") == "yes")
    log("")
    log("=" * 60)
    log(f"DONE in {time.time() - _START:.1f}s")
    log(f"  pages this run    : {pages_done} processed, {pages_skipped} skipped (already done)")
    log(f"  new crops written : {crops_new}")
    log(f"  manifest total    : {len(merged)} rows -> {manifest_path}")
    if args.index:
        log(f"  titles to review  : {nrev} (review=yes) across this run's sources")
    log("=" * 60)
    log("Safe to cancel/resume: re-running the same command skips finished pages.")
    log("Next: check rows where review=yes (fix 'title' only if wrong), then run:")
    log(f"    python {os.path.basename(__file__)} --apply {manifest_path}")


def _save_pdf(gray, path):
    ok, buf = cv2.imencode(".png", gray)
    try:
        from PIL import Image
        import io
        Image.open(io.BytesIO(buf.tobytes())).convert("RGB").save(path, "PDF")
    except Exception:
        cv2.imwrite(path.replace(".pdf", ".png"), gray)


def cmd_apply(args):
    mpath = args.apply
    outdir = os.path.dirname(os.path.abspath(mpath))
    with open(mpath, newline="") as f:
        rows = list(csv.DictReader(f))
    log(f"applying titles from {mpath}  ({len(rows)} rows)")
    renamed = missing = unchanged = 0
    for r in rows:
        cur = os.path.join(outdir, r["current_file"])
        if not os.path.exists(cur):
            log(f"  !! missing file, skipped: {r['current_file']}")
            missing += 1
            continue
        ext = os.path.splitext(r["current_file"])[1]
        title = slugify(r["title"]) or "UNTITLED"
        new = f"{r['page']}_{title}{ext}"
        # de-duplicate if two tunes share a name
        dst = os.path.join(outdir, new)
        k = 2
        while os.path.exists(dst) and os.path.abspath(dst) != os.path.abspath(cur):
            dst = os.path.join(outdir, f"{r['page']}_{title}_{k}{ext}"); k += 1
        if os.path.abspath(dst) == os.path.abspath(cur):
            unchanged += 1
        else:
            os.rename(cur, dst)
            renamed += 1
            log(f"  {os.path.basename(cur)} -> {os.path.basename(dst)}")
        r["current_file"] = os.path.basename(dst)
    with open(mpath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    log(f"done: {renamed} renamed, {unchanged} already correct, {missing} missing.")


def main():
    ap = argparse.ArgumentParser(description="Crop jazz tunes from scanned chord-grid PDFs.")
    ap.add_argument("inputs", nargs="*", help="input PDF(s)")
    ap.add_argument("--out", default="./tunes")
    ap.add_argument("--start-page", default="auto")
    ap.add_argument("--index", help="book index PDF (or .txt) giving the exact "
                    "title + printed page for every tune; titles are matched to it")
    ap.add_argument("--page-window", type=int, default=0,
                    help="also consider index titles from +/-N neighbouring pages "
                    "(use 1 if the printed page numbers may be off by one)")
    ap.add_argument("--review-below", type=float, default=0.55,
                    help="flag a title 'review' in the manifest when match "
                    "confidence is below this (default 0.55)")
    ap.add_argument("--work-width", type=int, default=1654,
                    help="internal detection width in px; every page is resampled "
                    "to this for detection so results are resolution-independent "
                    "(crops are still saved at native resolution). Default 1654.")
    ap.add_argument("--merge-below", type=float, default=0.18,
                    help="a non-first detection whose best index-title match is "
                    "below this confidence is treated as a misread chord row and "
                    "merged into the previous tune (false-band repair). Default 0.18.")
    ap.add_argument("--format", choices=["png", "pdf"], default="png")
    ap.add_argument("--no-sidebar", action="store_true")
    ap.add_argument("--full-width", action="store_true",
                    help="do not crop horizontally: keep the entire page width "
                    "(includes the discography column and both margins)")
    ap.add_argument("--keep-crossref", action="store_true",
                    help="also output titled regions that have NO chord grid "
                    "(cross-references like 'X -> SEE Y', and grid-less tunes). "
                    "Default: skip them.")
    ap.add_argument("--pad", type=int, default=12)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--apply", help="apply corrected titles from a manifest.csv")
    args = ap.parse_args()
    if args.apply:
        cmd_apply(args)
    elif args.inputs:
        cmd_detect(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
