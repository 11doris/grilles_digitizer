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
  * Locating each tune and its crop rectangle. This uses page STRUCTURE
    (box-grid geometry), not text recognition, so it is robust to the poor
    scan quality.
  * TITLES, when you supply the book's alphabetical index (--index). The index
    lists the exact title and printed page of every tune. The script OCRs each
    hand-lettered title only well enough to pick the right one out of the few
    the index lists for that page, then uses the index's exact spelling. Each
    title gets a confidence; only low-confidence ones are flagged for review.

WHAT IS NOT RELIABLE FROM THIS SCAN
  * Reading a title from scratch without the index (OCR gives CHAIR->"LHATR").
    Without --index the raw OCR guess is written to the manifest for you to fix.
  * The tiny printed PAGE NUMBER. Give the first printed page with --start-page
    (it is then incremented per page); the index is keyed on that number.

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
print(f"HAVE_TESS: {HAVE_TESS}")

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
    # Vertical kernel 85 deliberately ignores hand-lettered TITLE strokes
    # (~55-75 px) so titles are never absorbed into the grid.
    hl = _open(ink, 150, 1)
    vl = _open(ink, 1, 85)
    Hd = cv2.dilate(hl, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 70)))
    Vd = cv2.dilate(vl, cv2.getStructuringElement(cv2.MORPH_RECT, (70, 1)))
    grid = cv2.bitwise_and(Hd, Vd)
    prof = (grid[:, x0:x1] > 0).sum(1).astype(float) / max(cw, 1)
    isg = (prof > 0.10).astype(np.uint8).reshape(-1, 1)
    isg = cv2.morphologyEx(isg, cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (1, 55))).ravel() > 0

    runs, cur, s = [], isg[0], 0
    for y in range(1, H):
        if isg[y] != cur:
            runs.append((cur, s, y)); cur, s = isg[y], y
    runs.append((cur, s, H))
    blocks = [(s, e) for v, s, e in runs if v and e - s > 60]
    if not blocks:
        return g, (x0, x1), H, []

    # --- de-lined text image (vertical kernel 80 keeps title strokes intact).
    vlT = _open(ink, 1, 80)
    txt = cv2.subtract(ink, cv2.bitwise_or(hl, vlT))
    txt = _open(txt, 2, 2)
    leftw = int(cw * 0.46)

    def title_in(gs, ge, hmin=46):
        a, b = max(0, gs - 6), min(H, ge + 8)
        if b - a < 40:
            return None
        left = txt[a:b, x0:x0 + leftw]
        rs, rl = _tallest_run((left > 0).sum(1) > 4)
        if rl < hmin:
            return None
        region = left[rs:rs + rl]
        width = int(((region > 0).sum(0) > 0).sum())
        if width < 120:                 # a title spans many columns
            return None
        return a + rs, rl

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


def load_index(path):
    """Parse the book's index PDF/text into {page:int -> [full titles]}.

    Accepts the index PDF (uses `pdftotext`) or a pre-extracted .txt file.
    Returns {} on any failure so detection can proceed without it.
    """
    try:
        if path.lower().endswith((".txt",)):
            text = open(path, encoding="utf-8", errors="replace").read()
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name
            subprocess.run(["pdftotext", path, tmp], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            text = open(tmp, encoding="utf-8", errors="replace").read()
            os.unlink(tmp)
    except Exception as e:
        print(f"!! could not read index {path}: {e}", file=sys.stderr)
        return {}
    by_page, buf = {}, ""
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            buf = ""; continue
        if _ONLYNUM.match(s):                 # index's own page-header digits
            buf = ""; continue
        cand = (buf + " " + s).strip() if buf else s
        m = _LEAD.match(cand) or _GLUE.match(cand)
        if m:
            title = re.sub(r'\s+', ' ', m.group(1)).strip(" .")
            if title:
                by_page.setdefault(int(m.group(2)), []).append(title)
            buf = ""
        else:
            buf = cand                        # wrapped (long) title, keep building
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

    rows = []
    done_pages = 0
    for fi, pdf in enumerate(args.inputs, 1):
        base_page = parse_start_page(args.start_page, pdf)
        npages = page_counts.get(pdf)
        log("")
        log(f"FILE {fi}/{len(args.inputs)}: {os.path.basename(pdf)}  "
            f"({npages if npages is not None else '?'} pages, "
            f"printed start page = {base_page if base_page is not None else 'auto'})")
        for pidx, gray in pdf_page_images(pdf):
            done_pages += 1
            if gray is None:
                log(f"  !! page {pidx + 1}: could not read image -- skipped")
                continue
            page_no = (base_page + pidx) if base_page is not None else f"{os.path.splitext(os.path.basename(pdf))[0]}-p{pidx+1}"
            pct = f"{100*done_pages/grand_total:4.0f}%" if grand_total else "  ? "
            log(f"  [{pct} | page {done_pages}/{grand_total or '?'}] "
                f"file page {pidx+1}/{npages or '?'}  printed# {page_no}  "
                f"(image {gray.shape[1]}x{gray.shape[0]}) -- detecting tunes...")
            g, (x0, x1), H, tunes = detect_tunes(gray, args.keep_crossref)
            log(f"        found {len(tunes)} tune(s); reading + matching titles...")
            right = x1 if not args.no_sidebar else grid_right_edge(to_ink(gray)[1], x0, x1) + 8
            if args.debug:
                vis = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)

            # Pass 1: read every (noisy) title on the page.
            ocr_list = [ocr_title(gray, top, rl, x0, x1) for (top, bot, rl) in tunes]

            # Pass 2: snap titles to the book index for this printed page. The
            # index gives the exact spellings; OCR only has to disambiguate
            # among the few titles the index lists for the page.
            cands = []
            if isinstance(page_no, int):
                for dp in range(0, args.page_window + 1):
                    for p in ({page_no} if dp == 0 else {page_no - dp, page_no + dp}):
                        cands.extend(index.get(p, []))
            if cands:
                matched = match_titles(ocr_list, cands)
            else:
                matched = [(t, 0.0) for t in ocr_list]

            for ti, ((top, bot, rl), ocr_raw, (full, conf)) in enumerate(
                    zip(tunes, ocr_list, matched), 1):
                title = main_title(full) if cands else ocr_raw
                review = "yes" if (conf < args.review_below or not _squash(title)) else ""
                pad = args.pad
                y0c, y1c = max(0, top - pad), min(H, bot + pad)
                if args.full_width:
                    x0c, x1c = 0, g.shape[1]          # no horizontal cropping
                else:
                    x0c, x1c = max(0, x0 - pad), min(g.shape[1], right + pad)
                crop = g[y0c:y1c, x0c:x1c]
                if args.scale != 1.0:
                    crop = cv2.resize(crop, None, fx=args.scale, fy=args.scale,
                                      interpolation=cv2.INTER_CUBIC)
                prov = f"{page_no}_{ti:02d}_{slugify(title) or 'UNTITLED'}"
                fn = prov + ("." + args.format)
                outp = os.path.join(args.out, fn)
                if args.format == "pdf":
                    _save_pdf(crop, outp)
                else:
                    cv2.imwrite(outp, crop)
                rows.append(dict(source=os.path.basename(pdf), page=page_no,
                                 index=ti, title=title, conf=f"{conf:.2f}",
                                 review=review, ocr_raw=ocr_raw, alt_title=full,
                                 y0=y0c, y1=y1c, x0=x0c, x1=x1c, current_file=fn))
                if args.debug:
                    cv2.rectangle(vis, (x0c, y0c), (x1c, y1c), (0, 0, 255), 5)
                    cv2.putText(vis, (title or "?")[:24], (x0c + 8, y0c + 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 120, 255), 3)
                flag = "  <-- REVIEW" if review else ""
                log(f"        tune {ti}: conf {conf:.2f}  ocr={ocr_raw[:24]!r} "
                    f"-> {title!r}  [{x1c-x0c}x{y1c-y0c}px] saved {fn}{flag}")
            if args.debug:
                dp = os.path.join(args.out, f"{page_no}_debug.png")
                s = 800 / g.shape[1]
                cv2.imwrite(dp, cv2.resize(vis, (800, int(g.shape[0] * s))))
                log(f"        wrote debug overlay {os.path.basename(dp)}")
    # Merge with any existing manifest in this folder so that running several
    # PDFs into one --out dir (e.g. with different --start-page values) builds a
    # single complete manifest. Rows from PDFs processed in THIS run replace
    # their old entries; rows from other PDFs are preserved.
    fieldnames = ["source", "page", "index", "title", "conf", "review",
                  "ocr_raw", "alt_title", "y0", "y1", "x0", "x1", "current_file"]
    sources_now = {os.path.basename(p) for p in args.inputs}
    merged = []
    if os.path.exists(manifest_path):
        with open(manifest_path, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("source") not in sources_now:
                    merged.append(r)
    merged.extend(rows)
    with open(manifest_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(merged)
    nrev = sum(1 for r in rows if r.get("review") == "yes")
    log("")
    log("=" * 60)
    log(f"DONE in {time.time() - _START:.1f}s")
    log(f"  pages processed   : {done_pages}")
    log(f"  crops written     : {len(rows)}")
    log(f"  manifest total    : {len(merged)} -> {manifest_path}")
    if args.index:
        log(f"  titles confident  : {len(rows) - nrev}")
        log(f"  titles to review  : {nrev} (review=yes, conf < {args.review_below})")
    log("=" * 60)
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
    ap.add_argument("--format", choices=["png", "pdf"], default="png")
    ap.add_argument("--no-sidebar", action="store_true")
    ap.add_argument("--full-width", action="store_true",
                    help="do not crop horizontally: keep the entire page width "
                    "(includes the discography column and both margins)")
    ap.add_argument("--keep-crossref", action="store_true")
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
