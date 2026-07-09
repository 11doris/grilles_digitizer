#!/usr/bin/env python3
"""
melody_cropper.py  -  Stage 0: page/tune extraction from the AGJ melody book
============================================================================

Stage 0 of the melody pipeline (one python file per stage; see
docs/specs/melody_digitizer_spec.md). It
turns pages of `AGJ_Melody.pdf` (hand-written lead sheets, several tunes per
page) into one PNG per tune in `data/melody/01_crops/`, plus a `melody_manifest.json`
mapping each crop to a canonical tune (title + grille page) for manual review.

Unlike the chord-grille pages (crop_tunes.py), melody pages have NO chord grid:
each tune is a hand-lettered TITLE line followed by a stack of 5-line staves
(with chord letters written BELOW each staff). Tune boundaries are therefore
found from the staves + the title lines, not from grid boxes:

  1. Staff systems: long horizontal strokes (staff lines) grouped into ~110px
     bands (reuse crop_tunes de-lining kernels + to_ink polarity).
  2. Tune starts: a staff begins a new tune when the strip just ABOVE it holds a
     wide centred run of hand-lettering (the title) AND/OR sits below an unusually
     large vertical gap. Chord letters hug the staff ABOVE them, so they never
     look like a title sitting just above the NEXT staff -- that asymmetry is the
     discriminator. Both cues are recorded so low-confidence splits flag review.
  3. Each tune is cropped from just above its title to just above the next tune's
     title; the last tune runs to the bottom of its music, dropping any trailing
     lyrics paragraph (a short annotation is kept -- see last_tune_bottom).

The melody book's page numbers differ from the grille book's, so a crop cannot
be named with its grille id directly. Titles are OCR'd (reuse ocr_title) and
fuzzy-matched against the whole book index (AGJ_index.pdf) to recover the
canonical title + grille page; the crop is named `<melpage>_<idx>_<TITLE>.png`
and the manifest records the matched grille page/id for the eventual join to
`data/chords/02_raw/<id>.json`. Low match confidence -> review=yes.

USAGE
  python pipelines/melody/melody_cropper.py sources/AGJ_Melody.pdf --pages 847,935,939 \
         --melody-index sources/AGJ_Melody_Index.pdf --index sources/AGJ_index.pdf \
         --out data/melody/01_crops --debug
  python pipelines/melody/melody_cropper.py sources/AGJ_Melody.pdf --pages 7..972 \
         --melody-index sources/AGJ_Melody_Index.pdf --index sources/AGJ_index.pdf
"""
import argparse, json, os, re, subprocess, sys, tempfile, time

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root

# Reuse the grille machinery: page extraction, polarity fix, morphology,
# title OCR, and the fuzzy index matcher.
from pipelines.chords.crop_tunes import (count_pages, extract_page, to_ink, _open, _index_columns,
                                         ocr_title, load_index, _title_score, slugify, main_title,
                                         write_png_1bit)

_START = time.time()


def log(msg=""):
    print(f"[{time.time() - _START:6.1f}s] {msg}", flush=True)


def write_png(path, img):
    """cv2.imwrite that doesn't fail silently. On Windows a transient file lock
    (viewer/indexer/AV) makes imwrite return False without raising, which would
    leave a stale crop from a previous run on disk. Retry briefly, then raise."""
    for attempt in range(4):
        if cv2.imwrite(path, img):
            return
        time.sleep(0.25)
    raise IOError(f"could not write {path} (locked?)")


# ---------------------------------------------------------------------------
# Deskew: the staff-line machinery (music_x_bounds, staff_bands) relies on
# ~150px runs of long HORIZONTAL ink, so even ~1 degree of scan rotation makes a
# staff line drift off its row and vanish from the H150 mask -- staves collapse
# or merge. We straighten the page first by finding the rotation that packs the
# ink into the sharpest horizontal rows (staff lines become tall projection
# peaks only when they are level).
# ---------------------------------------------------------------------------
def estimate_skew(ink, max_deg=4.0, step=0.25):
    """Rotation (deg, CCW positive) that best levels the staff lines.

    Rotate a downscaled ink mask across +/-max_deg and keep the angle whose row
    projection is most peaked (sum of squared row sums): aligned staff lines pile
    all their ink into a few rows, maximising that energy."""
    small = cv2.resize((ink > 0).astype(np.float32), None, fx=0.25, fy=0.25,
                       interpolation=cv2.INTER_AREA)
    h, w = small.shape
    c = (w / 2, h / 2)
    best_a, best_score = 0.0, -1.0
    for a in np.arange(-max_deg, max_deg + 1e-9, step):
        R = cv2.getRotationMatrix2D(c, float(a), 1.0)
        rot = cv2.warpAffine(small, R, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
        score = float((rot.sum(1) ** 2).sum())
        if score > best_score:
            best_score, best_a = score, float(a)
    return best_a


def deskew(native, angle):
    """Rotate the scan by `angle` deg about its centre, padding with white."""
    h, w = native.shape[:2]
    R = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(native, R, (w, h), flags=cv2.INTER_LINEAR,
                          borderValue=(255, 255, 255))


# ---------------------------------------------------------------------------
# Geometry: staff systems and the music x-extent.
# ---------------------------------------------------------------------------
def music_x_bounds(ink):
    """Horizontal extent of the staff lines = the music column.

    Uses the long-horizontal-stroke mask so the spiral binding (round blobs) and
    black scan margins -- which are not long horizontal rules -- are excluded."""
    hl = _open(ink, 150, 1)
    col = (hl > 0).sum(0)
    if col.max() == 0:
        return 0, ink.shape[1]
    xs = np.where(col > col.max() * 0.15)[0]
    return int(xs.min()), int(xs.max())


def staff_bands(ink, x0, x1):
    """Group staff lines into one (top, bottom) band per staff system.

    A staff line is a row rich in long horizontal ink; the 5 lines of a system
    (spaced ~24px) are within 40px of each other so they merge into one ~110px
    band, while inter-system gaps (>>40px) separate systems."""
    hl = _open(ink, 150, 1)
    prof = (hl[:, x0:x1] > 0).sum(1).astype(float)
    if prof.max() == 0:
        return []
    isline = prof > prof.max() * 0.30
    H = len(isline)
    bands, y = [], 0
    while y < H:
        if not isline[y]:
            y += 1
            continue
        last = y
        k = y
        while k < H:
            if isline[k]:
                last = k
            elif k - last > 40:
                break
            k += 1
        bands.append((y, last))
        y = k
    # a real system is ~100-120px tall; drop the top/bottom scan-edge slivers
    return [(a, b) for (a, b) in bands if b - a >= 70]


# ---------------------------------------------------------------------------
# Width crop: trim the page to the staves without clipping any music.
# ---------------------------------------------------------------------------
# We keep the FULL page height per tune, but narrow each crop to the music
# column so the white side margins and the spiral binding drop away. The anchor
# is the staff lines' own horizontal extent [sl, sr]; everything that reaches
# past it -- title ends, chord-symbol tails, 1st/2nd-ending brackets -- sits only
# a few dozen px beyond, while the binding sits >=~200px out behind a clear white
# gap. So a generous fixed pad keeps every overhang yet still lands inside that
# gap. A defensive clamp then refuses to cross a near-edge full-height binding
# column, in case a particular page's gap is tighter than the pad.
XPAD = 150             # px kept beyond the staff lines on each side
BIND_HFRAC = 0.32      # ink-column height fraction that marks a binding bar
BIND_GAP = 20          # stay this many px clear of a binding column


def staff_x_extent(ink, bands):
    """Robust horizontal extent of the staff lines themselves, as (left, right).

    music_x_bounds thresholds one global column profile and, on pages whose
    binding hugs the music, gets dragged out to the page edge (its H150 mask
    catches the binding's black bar). Here we measure each staff BAND's own
    long-horizontal run and take the median left/right across bands: the binding
    never spans a band's rows, so its blobs cannot move the median."""
    hl = _open(ink, 150, 1)
    L, R = [], []
    for a, b in bands:
        row = (hl[a:b] > 0).sum(0)
        if row.max() == 0:
            continue
        xs = np.where(row > row.max() * 0.2)[0]
        L.append(int(xs.min()))
        R.append(int(xs.max()))
    if not L:
        return music_x_bounds(ink)
    return int(np.median(L)), int(np.median(R))


def music_x_crop(ink, bands):
    """Left/right crop columns focusing on the staves (pad kept, binding dropped)."""
    H, W = ink.shape
    sl, sr = staff_x_extent(ink, bands)
    # Degenerate detection (e.g. a page where only a binding sliver was read as a
    # staff): the "staff" spans a tiny fraction of the page. Don't trust it -- keep
    # the full width so the crop stays usable for manual review.
    if sr - sl < 0.4 * W:
        return 0, W
    xL, xR = sl - XPAD, sr + XPAD
    # Never cross the spiral binding: find near-edge columns that are almost
    # full-page-height ink (a binding bar) and stop just short of them.
    tall = (ink > 0).sum(0) > BIND_HFRAC * H
    lo, hi = int(W * 0.33), int(W * 0.67)
    left = np.where(tall[:lo])[0]
    right = np.where(tall[hi:])[0]
    if len(left):
        xL = max(xL, int(left.max()) + BIND_GAP)
    if len(right):
        xR = min(xR, hi + int(right.min()) - BIND_GAP)
    return max(0, xL), min(W, xR)


def deline_text(ink):
    """Ink with staff lines (H150) and stems/barlines (V60) removed -> lettering
    (titles, chords, note dots) plus small note fragments."""
    txt = cv2.subtract(ink, cv2.bitwise_or(_open(ink, 150, 1), _open(ink, 1, 60)))
    return _open(txt, 2, 2)


def merged_text_comps(txt, close_w=45):
    """Connected components of the de-lined text after a horizontal close.

    The close (~45px) fuses letters within a WORD but not chord symbols spaced a
    bar apart, so a title word becomes one wide component while a chord row stays
    a set of narrow ones. Returns (top, bot, left, right, w, h) per component."""
    m = cv2.morphologyEx(txt, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (close_w, 1)))
    n, _lab, st, _c = cv2.connectedComponentsWithStats((m > 0).astype(np.uint8), 8)
    out = []
    for i in range(1, n):
        w = st[i, cv2.CC_STAT_WIDTH]
        h = st[i, cv2.CC_STAT_HEIGHT]
        a = st[i, cv2.CC_STAT_AREA]
        t = st[i, cv2.CC_STAT_TOP]
        l = st[i, cv2.CC_STAT_LEFT]
        if 34 <= h <= 120 and a >= 400:
            out.append((t, t + h, l, l + w, w, h))
    return out


# ---------------------------------------------------------------------------
# Tune-start detection.
# ---------------------------------------------------------------------------
# A staff starts a new tune when a large vertical gap precedes it (GAP): a new
# tune needs room for its title, so within-tune staves (their gap filled by the
# previous staff's chord row) sit at the page's baseline spacing, while a real
# start clears it. That baseline spacing VARIES per page (~120-175px depending on
# how many staves are packed on), so a fixed threshold either over-splits dense
# pages or misses tight ones. Instead we threshold RELATIVE to the page's own
# median inter-staff gap: within-tune gaps stay below ~1.2x the median, real
# starts run >2x it, so GAP_RATIO*median lands safely between (with an absolute
# floor for pages too small to estimate a median). The title's width (WFRAC) is
# only a CORROBORATING cue -- it cannot mark a start on its own, because a
# full-width chord row ("F Dm Gm7 C7 ...") written below one staff drops into the
# next staff's title zone and trips wfrac mid-tune (and can even sit nearer the
# staff below than above). So a gap-start that also carries a wide title hugging
# its own staff is high-confidence; a gap alone is flagged for review.
WFRAC_START = 0.27     # merged title width / music width to corroborate a start
GAP_RATIO = 1.4        # a start gap exceeds this multiple of the median staff gap
GAP_FLOOR = 160        # absolute floor for the start gap (small/odd pages)
# Title sits in rows [staff_top-220, staff_top-8]. The upper reach is generous
# (220px, not ~one staff-gap) because a "Verse"/"Chorus"/"Bebop Intro" tempo line
# or a section box often sits BETWEEN the title and the staff, pushing the title
# ~160-190px up. That only risks catching the previous staff's chord row on a
# real start, but a real start's gap is large (>=300px), so that chord row stays
# well above 220px and out of the zone.
TITLE_ZONE = (220, 8)


def find_tune_starts(bands, comps, cw):
    """Return per tune-start staff: dict(idx, title_top, title_bot, wfrac, gap,
    conf). bands = staff (top,bot); comps = merged text components; cw = music
    width."""
    starts = []
    # Per-page start threshold: a multiple of the typical (median) staff spacing.
    igaps = [bands[i][0] - bands[i - 1][1] for i in range(1, len(bands))]
    gap_start = max(GAP_FLOOR, np.median(igaps) * GAP_RATIO) if igaps else GAP_FLOOR
    for i, (a, b) in enumerate(bands):
        # widest merged text component sitting in the title zone above this staff
        best = None
        for (t, bt, l, r, w, h) in comps:
            yc = (t + bt) / 2
            if a - TITLE_ZONE[0] <= yc <= a - TITLE_ZONE[1]:
                if best is None or w > best[4]:
                    best = (t, bt, l, r, w, h)
        wfrac = (best[4] / cw) if best else 0.0
        gap = a - bands[i - 1][1] if i > 0 else a   # gap above (page top for i==0)

        if i == 0:
            is_start, conf = True, 1.0               # first staff always a tune
        else:
            # Require the gap: it is the reliable start signal (see note above).
            if gap < gap_start:
                continue
            # wfrac only corroborates, and only when the wide component hugs THIS
            # staff (a real title) rather than the previous one (a chord row).
            title_hug = best is not None and (a - best[1]) < (best[0] - bands[i - 1][1])
            wide = wfrac >= WFRAC_START and title_hug
            conf = 0.95 if wide else 0.6             # gap alone -> review

        if best:
            title_top, title_bot = best[0], best[1]
        else:
            title_top, title_bot = max(0, a - TITLE_ZONE[0]), a - TITLE_ZONE[1]
            if i == 0:
                title_top = 0                        # clipped title may touch top
        starts.append(dict(idx=i, title_top=title_top, title_bot=title_bot,
                           wfrac=round(wfrac, 3), gap=int(gap), conf=conf))
    return starts


# A tune's last staff is followed by its chord-letter row; anything more below
# that is either a short annotation (keep) or a lyrics paragraph (omit). We
# classify by the total text height BELOW the chord row: one short line is an
# annotation, a taller block is lyrics.
LYRICS_MIN_H = 180     # px of text below the chord row that marks a lyrics block


def text_lines_below(ink, x0, x1, y_from):
    """Contiguous inky row-runs (text lines) below y_from in the central music
    column, as (top, bot). Uses the de-lined image so stray marks don't count."""
    txt = deline_text(ink)
    cw = x1 - x0
    cx0, cx1 = x0 + int(0.06 * cw), x1 - int(0.06 * cw)
    on = (txt[:, cx0:cx1] > 0).sum(1) > (cx1 - cx0) * 0.02
    H = len(on)
    lines, y = [], y_from + 5
    while y < H:
        if not on[y]:
            y += 1
            continue
        s = y
        while y < H and on[y]:
            y += 1
        if y - s >= 12:                    # ignore 1-2px speckle rows
            lines.append((s, y))
    return lines


def last_tune_bottom(ink, x0, x1, b_last, pad, staff_gap):
    """Bottom y for the last tune on a page: keep the chord row under the last
    staff and any short trailing annotation, but drop a lyrics paragraph.

    The chord row hugs the last staff, within about one inter-staff gap below it.
    Sparse chords (e.g. "F  C7  F") don't form a solid text line and would be
    under-measured, so we never cut inside that band -- we protect [b_last,
    b_last+staff_gap] wholesale and only classify text found BELOW it: a tall
    block is a trailing lyrics paragraph (omit), anything short is an annotation
    (keep)."""
    H = ink.shape[0]
    floor = min(H, b_last + staff_gap)           # bottom of the guaranteed chord band
    below = text_lines_below(ink, x0, x1, floor)
    if not below:
        return min(H, floor + pad), None
    extra = sum(e - s for s, e in below)
    if extra > LYRICS_MIN_H:                      # lyrics paragraph -> omit it
        cut = min(H, max(floor, below[0][0] - 6))  # cut between chords and lyrics
        return cut, (below[0][0], below[-1][1])
    return min(H, below[-1][1] + pad), None       # short annotation -> keep it


# ---------------------------------------------------------------------------
# Title text -> canonical title via the book index.
# ---------------------------------------------------------------------------
def load_melody_index(path):
    """Parse AGJ_Melody_Index.pdf into a flat list of canonical titles.

    Unlike the grille index (crop_tunes.load_index), the melody index carries NO
    page numbers -- it is a plain alphabetical listing, 3 columns per page sorted
    within each column. We only need the complete SET of titles to fuzzy-match
    against, so the reading order across columns/pages is irrelevant: detect the
    column x-offsets, bucket every text cell into its column, and keep each
    non-empty cell as a title. Returns [] on any failure."""
    try:
        if path.lower().endswith(".txt"):
            text = open(path, encoding="utf-8", errors="replace").read()
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name
            subprocess.run(["pdftotext", "-layout", path, tmp], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            text = open(tmp, encoding="utf-8", errors="replace").read()
            os.unlink(tmp)
    except Exception as e:
        print(f"!! could not read melody index {path}: {e}", file=sys.stderr)
        return []
    lines = text.splitlines()
    cols = _index_columns(lines)

    def col_of(start):
        return min(range(len(cols)), key=lambda i: abs(start - cols[i]))

    streams = [[] for _ in cols]
    for ln in lines:
        if not ln.strip():
            continue
        for m in re.finditer(r"\S.*?(?=\s{2,}|$)", ln):
            streams[col_of(m.start())].append(m.group().strip())
    titles, seen = [], set()
    for st in streams:
        for cell in st:
            c = re.sub(r"\s+", " ", cell).strip()
            key = re.sub(r"[^A-Za-z]", "", c).upper()
            if key and not c.isdigit() and key not in seen:
                seen.add(key)
                titles.append(c)
    return titles


def match_index(ocr, index_titles):
    """Fuzzy-match an OCR'd title against every (title, page) in the book index.
    Returns (canonical_title, grille_page, score, margin) where margin is the
    score gap to the runner-up title -- a small margin means the match is
    ambiguous (common when hand-lettered OCR is garbled) and should be reviewed."""
    scored = sorted(((_title_score(ocr, t), t, p) for t, p in index_titles),
                    reverse=True)
    if not scored:
        return ("", None, 0.0, 0.0)
    s1, t1, p1 = scored[0]
    s2 = scored[1][0] if len(scored) > 1 else 0.0
    return (t1, p1, s1, s1 - s2)


# ---------------------------------------------------------------------------
# Stage 0 driver.
# ---------------------------------------------------------------------------
def parse_pages(spec):
    """'847,935,939' or '7..972' or a mix -> sorted list of ints."""
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ".." in part:
            a, b = part.split("..")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def resolve_grille_page(canon, grille_titles, thr=0.80):
    """Look the canonical title up in the grille index (title -> grille page) so
    the manifest keeps the link to tunes/<id>.json. Titles are clean on both
    sides, so a high-confidence fuzzy hit is required. Returns page or None."""
    if not grille_titles:
        return None
    t, p, s, _m = match_index(canon, grille_titles)
    return int(p) if (p is not None and s >= thr) else None


def process_page(pdf, page_no, start_page, title_target, grille_titles, args, vis_dir):
    """Detect + crop every tune on one melody page. Returns list of manifest
    rows. `title_target` = [(title, page-or-None)] fuzzy-match source (the melody
    index, preferred); `grille_titles` resolves the grille page for the link."""
    pidx = page_no - start_page
    native = extract_page(pdf, pidx)
    if native is None:
        log(f"  page {page_no}: could not extract image")
        return []
    g, ink = to_ink(native)
    # Straighten a rotated scan before any staff-line detection (see estimate_skew).
    skew = estimate_skew(ink)
    if abs(skew) >= 0.25:
        native = deskew(native, skew)
        g, ink = to_ink(native)
        log(f"  page {page_no}: deskewed {skew:+.2f} deg")
    H, W = ink.shape
    x0, x1 = music_x_bounds(ink)
    cw = x1 - x0
    bands = staff_bands(ink, x0, x1)
    if not bands:
        log(f"  page {page_no}: no staves detected")
        return []
    comps = merged_text_comps(deline_text(ink))
    starts = find_tune_starts(bands, comps, cw)
    pad = args.pad
    # typical within-tune staff spacing (robust to the few big inter-tune gaps);
    # the last staff's chord row lives within one such gap below it.
    gaps = [bands[i][0] - bands[i - 1][1] for i in range(1, len(bands))]
    staff_gap = int(np.median(gaps)) if gaps else 130
    # bottom of the last tune, with any lyrics paragraph dropped
    page_bottom, lyrics_yr = last_tune_bottom(ink, x0, x1, bands[-1][1], pad, staff_gap)
    # narrow each crop to the music column: drop the white side margins and the
    # spiral binding while keeping every overhang (titles/chords/ending tails).
    xL, xR = music_x_crop(ink, bands)

    log(f"  page {page_no}: {len(bands)} staves -> {len(starts)} tune(s)")
    if args.debug:
        vis = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        for a, b in bands:
            cv2.rectangle(vis, (x0, a), (x1, b), (0, 0, 255), 2)

    rows = []
    for k, stt in enumerate(starts):
        y0 = max(0, stt["title_top"] - pad)
        is_last = k + 1 >= len(starts)
        if not is_last:
            y1 = max(y0 + 1, starts[k + 1]["title_top"] - pad)
        else:
            y1 = page_bottom
        crop = g[y0:y1, xL:xR]

        # OCR the title band and match it to the book index.
        th = max(40, stt["title_bot"] - stt["title_top"])
        ocr = ocr_title(native, stt["title_top"], th, x0, x1)

        def good(s, m):
            return s >= args.match_below and m >= args.margin_min

        # Match against the melody index first; if it has no confident hit (e.g.
        # the melody index is partial and lacks this title) fall back to the
        # grille index, which spans the whole book.
        canon, gpage, mscore, margin = match_index(ocr, title_target) if title_target \
            else (ocr, None, 0.0, 0.0)
        source = "melody" if title_target else ""
        if not good(mscore, margin) and grille_titles and title_target is not grille_titles:
            c2, p2, s2, m2 = match_index(ocr, grille_titles)
            if good(s2, m2) and s2 > mscore:
                canon, gpage, mscore, margin, source = c2, p2, s2, m2, "grille"
        good_match = bool(title_target) and good(mscore, margin)
        if good_match and gpage is None:
            gpage = resolve_grille_page(canon, grille_titles)
        title_for_name = canon if good_match else ocr
        slug = slugify(main_title(title_for_name)) or "UNTITLED"
        idx = k + 1
        fn = f"{page_no}_{idx:02d}_{slug}.png"
        write_png_1bit(os.path.join(args.out, fn), crop)

        # review if the split is shaky OR the title match is weak/ambiguous
        review = (stt["conf"] < 0.6) or (bool(title_target) and not good_match) \
            or (not slugify(main_title(title_for_name)))
        rows.append(dict(
            melody_page=int(page_no), index=int(idx), current_file=fn,
            ocr_title=ocr, matched_title=canon,
            grille_page=(int(gpage) if gpage is not None else None),
            match_source=(source if good_match else ""),
            match_score=round(float(mscore), 3), match_margin=round(float(margin), 3),
            split_conf=round(float(stt["conf"]), 3),
            wfrac=float(stt["wfrac"]), gap=int(stt["gap"]),
            review="yes" if review else "",
            lyrics_omitted=([int(lyrics_yr[0]), int(lyrics_yr[1])]
                            if (is_last and lyrics_yr) else None),
            y0=int(y0), y1=int(y1), x0=int(xL), x1=int(xR)))
        flag = "  <-- REVIEW" if review else ""
        if is_last and lyrics_yr:
            flag += f"  [lyrics y{lyrics_yr[0]}-{lyrics_yr[1]} omitted]"
        log(f"    tune {idx}: split_conf={stt['conf']:.2f} (wfrac={stt['wfrac']} "
            f"gap={stt['gap']}) ocr={ocr[:22]!r} -> {title_for_name!r} "
            f"[{source or '-'} gp{gpage} s={mscore:.2f}] {fn}{flag}")

        if args.debug:
            cv2.rectangle(vis, (xL, y0), (xR, y1),
                          (0, 180, 0) if not review else (0, 140, 255), 4)
            cv2.putText(vis, f"{idx}:{title_for_name[:22]}", (xL + 10, y0 + 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 0, 0), 3)
    if args.debug:
        s = 900 / W
        cv2.imwrite(os.path.join(vis_dir, f"{page_no}_debug.png"),
                    cv2.resize(vis, (900, int(H * s))))
    return rows


def run(args):
    os.makedirs(args.out, exist_ok=True)
    vis_dir = os.path.join(args.out, "debug")
    if args.debug:
        os.makedirs(vis_dir, exist_ok=True)

    log("=" * 60)
    log("melody_cropper (stage 0)")
    log(f"  pdf   : {args.pdf}")
    log(f"  out   : {os.path.abspath(args.out)}")

    # Primary title source: the melody book's own index (canonical spellings, no
    # page numbers). The grille index (with pages) is optional and used only to
    # resolve the grille page for the tunes/<id>.json link.
    mel_titles = load_melody_index(args.melody_index) if args.melody_index else []
    grille_index = load_index(args.index) if args.index else {}
    grille_titles = [(t, p) for p, ts in grille_index.items() for t in ts]
    if mel_titles:
        title_target = [(t, None) for t in mel_titles]
    else:
        title_target = grille_titles          # fall back to the grille index
    log(f"  melody index : {len(mel_titles)} titles")
    log(f"  grille index : {len(grille_titles)} titles (for grille-page link)")

    pages = parse_pages(args.pages)
    npages = count_pages(args.pdf)
    log(f"  pages : {len(pages)} ({pages[0]}..{pages[-1]})   pdf has {npages} pages")
    log("=" * 60)

    manifest_path = os.path.join(args.out, "melody_manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = {r["current_file"]: r for r in json.load(f)}

    for pg in pages:
        if npages is not None and not (args.start_page <= pg < args.start_page + npages):
            log(f"  page {pg}: outside PDF range, skipped")
            continue
        rows = process_page(args.pdf, pg, args.start_page, title_target,
                            grille_titles, args, vis_dir)
        # drop stale rows for this page, then add fresh ones
        manifest = {fn: r for fn, r in manifest.items() if r["melody_page"] != pg}
        for r in rows:
            manifest[r["current_file"]] = r
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(sorted(manifest.values(),
                             key=lambda r: (r["melody_page"], r["index"])),
                      f, indent=1, ensure_ascii=False)

    nrev = sum(1 for r in manifest.values() if r.get("review") == "yes")
    log("=" * 60)
    log(f"DONE in {time.time() - _START:.1f}s  "
        f"{len(manifest)} tune(s) total, {nrev} flagged review")
    log(f"  manifest -> {manifest_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Melody cropper (stage 0): extract one PNG per tune from "
                    "melody pages.")
    ap.add_argument("pdf", help="melody PDF (AGJ_Melody.pdf)")
    ap.add_argument("--pages", required=True,
                    help="pages to process, e.g. '847,935,939' or '7..972'")
    ap.add_argument("--start-page", type=int, default=1,
                    help="printed number of the PDF's first page (default 1: "
                    "page N == 1-based PDF page N)")
    ap.add_argument("--melody-index",
                    help="melody book index PDF/.txt (AGJ_Melody_Index.pdf): the "
                    "primary title source, matched against OCR'd titles")
    ap.add_argument("--index", help="grille index PDF/.txt (AGJ_index.pdf) with "
                    "page numbers, used to attach the grille page to each match")
    ap.add_argument("--out", default="data/melody/01_crops")
    ap.add_argument("--pad", type=int, default=14)
    ap.add_argument("--match-below", type=float, default=0.72,
                    help="index-match score below which the OCR title is used for "
                    "the filename and the row is flagged review (default 0.72; "
                    "hand-lettered title OCR is noisy, so the bar is high)")
    ap.add_argument("--margin-min", type=float, default=0.06,
                    help="minimum score gap to the runner-up index title; a smaller "
                    "gap means the match is ambiguous -> review (default 0.06)")
    ap.add_argument("--debug", action="store_true",
                    help="write annotated page overlays to <out>/debug/")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
