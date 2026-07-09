#!/usr/bin/env python3
"""
melody_straightener.py  -  Stage 1: staff detection & per-system straightening
===============================================================================

Stage 1 of the melody pipeline (one python file per stage; see
docs/specs/melody_digitizer_spec.md section 3). Input: per-tune crop PNGs
from `data/melody/01_crops/` (stage 0 output). Output, per tune, under
`data/melody/debug/<id>/`:

  strip_NN.png    straightened strip of system NN (band +/- PAD source rows,
                  every pixel COLUMN shifted vertically so the staff centre
                  sits on the fixed row `target`)
  overlay_NN.png  (--debug) strip with red staff lines / green space+ledger
                  dashes drawn at the persisted geometry -- the same overlay
                  style stage 4 sends to the model, here for human checking
  stage1.json     per-system geometry: band, staff x-extent, target, gap,
                  the 5 measured line y's, the warp (source staff-centre y
                  per window x, for mapping strip coords back to the source),
                  and a `flagged` verdict from the sanity check

The staves are hand-drawn with local slant AND curvature, so a global rotation
cannot level them (stage 0 already removed the page-level skew). Instead every
system is straightened per column: for 40px-wide windows every 20px the
vertical ink profile is cross-correlated with a 5-spike comb at the system's
line spacing, giving the staff-centre y as a function of x; each pixel column
is then shifted so that centre lands on `target`. All later geometry becomes
trivial: staff step of a strip y-coordinate = (target - y) / (gap/2), with
step 0 = B4 (middle line), +1 = C5, -2 = G4, ...

Sanity check per system: after straightening, the 5 line peaks of the row
profile must sit within +/-2 px of target + k*gap (k in -2..2); systems that
fail are flagged for model review in stage1.json.

USAGE
  python pipelines/melody/melody_straightener.py data/melody/01_crops/17_01_AINT_MISBEHAVIN.png --debug
  python pipelines/melody/melody_straightener.py data/melody/01_crops --limit 30
  python pipelines/melody/melody_straightener.py data/melody/01_crops/48*.png --out data/melody/debug
"""
import argparse, glob, json, os, sys, time

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root

from pipelines.chords.crop_tunes import to_ink, _open, write_png_1bit
from pipelines.melody.melody_cropper import write_png

_START = time.time()


def log(msg=""):
    print(f"[{time.time() - _START:6.1f}s] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Staff band detection (spec 3.1).
# ---------------------------------------------------------------------------
DARK_FRAC = 0.45       # row is a staff-line row if dark > this * page max
BAND_JOIN = 40         # line rows closer than this belong to one system
BAND_MIN_H = 60        # a 5-line system spans ~4*gap ~= 96px; drop slivers
BAND_MAX_H = 170       # ... and over-tall merges (two systems fused)


def staff_bands(ink):
    """(top, bottom) of each 5-line system, from the row-darkness histogram.

    Staff-line rows are far darker than anything else on the page (a line runs
    the full music width; lettering strokes don't), so a relative threshold on
    dark[y] finds them; rows within BAND_JOIN px merge into one band per
    system, which rides out the thickness/slant spread of hand-drawn lines."""
    dark = (ink > 0).sum(1).astype(float)
    if dark.max() == 0:
        return []
    isline = dark > DARK_FRAC * dark.max()
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
            elif k - last > BAND_JOIN:
                break
            k += 1
        bands.append((y, last))
        y = k
    return [(a, b) for (a, b) in bands if BAND_MIN_H <= b - a <= BAND_MAX_H]


def staff_x_extent(ink, band):
    """Horizontal extent (x0, x1) of THIS system's staff lines, via the
    long-horizontal-stroke mask restricted to the band's rows."""
    a, b = band
    hl = _open(ink[a:b + 1], 150, 1)
    col = (hl > 0).sum(0)
    if col.max() == 0:
        return 0, ink.shape[1]
    xs = np.where(col > col.max() * 0.2)[0]
    return int(xs.min()), int(xs.max())


def estimate_gap(ink, band, x0, x1):
    """Line spacing of one system, from its own line-row clusters.

    (b-a)/4 overestimates the gap by the line thickness + slant spread (pilot:
    24.5 vs a true 23.0), which weakens the comb's lock badly enough that
    beam-heavy windows prefer a one-line-off alignment. Instead cluster the
    band's dark rows into individual lines and take the median centre-to-
    centre spacing; falls back to (b-a)/4 when the lines don't separate."""
    a, b = band
    prof = (ink[a:b + 1, x0:x1] > 0).sum(1).astype(float)
    isline = prof > 0.5 * prof.max()
    centers, y = [], 0
    while y < len(isline):
        if not isline[y]:
            y += 1
            continue
        s = y
        while y < len(isline) and isline[y]:
            y += 1
        centers.append((s + y - 1) / 2.0)
    if len(centers) < 3:
        return (b - a) / 4.0
    d = np.diff(centers)
    gap = float(np.median(d))
    return gap if (b - a) / 6.0 < gap < (b - a) / 3.0 else (b - a) / 4.0


# ---------------------------------------------------------------------------
# Per-column staff-centre tracking (spec 3.2): comb cross-correlation.
# ---------------------------------------------------------------------------
WIN_W = 40             # window width for the vertical ink profile
WIN_STEP = 20          # window stride
SEARCH = 45            # comb centre searched within +/- this of the band centre
MIN_INK = 1000         # windows with less ink are unreliable -> interpolated
SMOOTH_K = 9           # median smoothing over window centres


def _medfilt(v, k):
    """Median filter with edge replication (no scipy dependency)."""
    if len(v) < 3:
        return v.copy()
    k = min(k, len(v) - (1 - len(v) % 2))       # odd, <= len
    p = k // 2
    padded = np.concatenate([np.full(p, v[0]), v, np.full(p, v[-1])])
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, k), axis=1)


VIT_STEP = 3           # max centre change (px) between adjacent windows
VIT_LAMBDA = 0.05      # smoothness cost per squared px of change


def track_centers(ink, band, gap):
    """Staff-centre y per window along x, by comb correlation + Viterbi.

    For each 40px window the vertical ink profile is scored against a 5-spike
    comb at spacing `gap` for every candidate centre within +/-SEARCH of the
    band centre. A hand-drawn staff drifts slowly (pilot: ~1 deg locally =
    <0.5px per 20px step), while the comb's failure mode is a ONE-LINE-OFF
    lock in beam-heavy stretches -- a ~gap-sized jump that a median filter
    passes when several windows in a row fail together. So the path through
    the per-window scores is chosen by dynamic programming with the step
    between adjacent windows capped at VIT_STEP px and penalised
    quadratically: curvature is nearly free, a one-line jump is prohibitive.
    Windows with too little ink score flat and are bridged by the smoothness
    prior alone.

    Returns (win_x, centers, interp_frac) or None when no window has enough
    ink (no staff to track)."""
    H, W = ink.shape
    a, b = band
    bc = (a + b) / 2.0
    r0 = max(0, int(bc - SEARCH - 2 * gap - 4))
    r1 = min(H, int(bc + SEARCH + 2 * gap + 5))
    region = (ink[r0:r1] > 0).astype(np.float32)
    rh = region.shape[0]
    # sliding-window profiles via a cumulative sum over x
    cum = np.concatenate([np.zeros((rh, 1), np.float32), np.cumsum(region, 1)], 1)
    cands = np.arange(bc - SEARCH, bc + SEARCH + 1.0)               # comb centres
    spikes = cands[:, None] + gap * np.arange(-2, 3)[None, :] - r0  # row coords
    rows = np.arange(rh, dtype=float)

    win_x, rewards, valid = [], [], []
    for x in range(0, W - WIN_W + 1, WIN_STEP):
        p = cum[:, x + WIN_W] - cum[:, x]
        win_x.append(x + WIN_W / 2)
        if p.sum() < MIN_INK:
            rewards.append(np.zeros(len(cands)))
            valid.append(False)
            continue
        score = np.interp(spikes.ravel(), rows, p).reshape(len(cands), 5).sum(1)
        rewards.append(score / score.max() if score.max() > 0 else score)
        valid.append(True)
    valid = np.array(valid)
    if not valid.any():
        return None

    # Viterbi over (window, candidate-centre) with capped quadratic steps.
    n, m = len(rewards), len(cands)
    deltas = range(-VIT_STEP, VIT_STEP + 1)
    V = rewards[0].copy()
    back = np.zeros((n, m), dtype=np.int16)
    for w in range(1, n):
        best = np.full(m, -np.inf)
        arg = np.zeros(m, dtype=np.int16)
        for d in deltas:
            lo, hi = max(0, -d), m - max(0, d)      # prev index = s + d
            cost = VIT_LAMBDA * d * d
            cand = V[lo + d:hi + d] - cost
            upd = cand > best[lo:hi]
            best[lo:hi][upd] = cand[upd]
            arg[lo:hi][upd] = np.arange(lo, hi, dtype=np.int16)[upd] + d
        V = best + rewards[w]
        back[w] = arg
    path = np.zeros(n, dtype=int)
    path[-1] = int(np.argmax(V))
    for w in range(n - 1, 0, -1):
        path[w - 1] = back[w, path[w]]

    centers = cands[path].astype(float)
    for w in range(n):                              # parabolic sub-pixel refine
        i, r = path[w], rewards[w]
        if valid[w] and 0 < i < m - 1:
            d2 = r[i - 1] - 2 * r[i] + r[i + 1]
            if d2 < 0:
                centers[w] += 0.5 * (r[i - 1] - r[i + 1]) / d2
    centers = _medfilt(centers, SMOOTH_K)
    return np.array(win_x), centers, float((~valid).mean())


# ---------------------------------------------------------------------------
# Straightening + sanity check (spec 3.2-3.3).
# ---------------------------------------------------------------------------
PAD = 100              # source rows kept above/below the band in the strip


def straighten(gray, band, win_x, centers):
    """Shift every pixel column so the staff centre lands on a fixed `target`.

    Integer shifts (no resampling) keep the 1-bit glyph shapes crisp. Returns
    (strip, target): strip spans band height + 2*PAD, staff centre at target
    = the strip's middle row."""
    H, W = gray.shape
    a, b = band
    hs = (b - a) + 2 * PAD
    target = hs // 2
    center_col = np.interp(np.arange(W), win_x, centers)
    shift = np.rint(center_col).astype(int) - target       # src_y = y + shift[x]
    ys = np.arange(hs)[:, None] + shift[None, :]
    inside = (ys >= 0) & (ys < H)
    strip = gray[np.clip(ys, 0, H - 1), np.arange(W)[None, :]]
    strip[~inside] = 255
    return strip, target


GAP_RANGE = (16.0, 33.0)   # plausible line spacings across the book


def measure_lines(strip_ink, x0, x1, anchor):
    """Measure the 5 line-peak y's of the straightened strip.

    The seed gap can be off by several px (estimate_gap falls back to
    (b-a)/4 when beams fuse its line clusters), and a +/-6px search around a
    wrong expectation latches onto beams. So the staff comb is fitted to the
    strip's own row profile by a dense 2-D search over (centre, gap) -- after
    straightening the 5 line peaks are sharp, making this unambiguous -- and
    only then is each individual peak refined sub-pixel near its fitted row.

    Returns (line_ys, quality) or (None, 0); quality = weakest line peak
    relative to the profile max (a real staff scores ~0.7+, garbage low)."""
    prof = (strip_ink[:, x0:x1] > 0).sum(1).astype(float)
    if prof.max() == 0:
        return None, 0.0
    rows = np.arange(len(prof), dtype=float)
    ks = np.arange(-2, 3)
    ts = anchor + np.arange(-12.0, 12.01, 0.5)
    gs = np.arange(GAP_RANGE[0], GAP_RANGE[1] + 1e-9, 0.25)
    spikes = ts[:, None, None] + gs[None, :, None] * ks[None, None, :]
    sc = np.interp(spikes.ravel(), rows, prof).reshape(len(ts), len(gs), 5).sum(2)
    i, j = np.unravel_index(int(np.argmax(sc)), sc.shape)
    t0, g0 = ts[i], gs[j]

    line_ys = []
    for k in ks:
        e = t0 + k * g0
        lo = max(0, int(round(e)) - 5)
        hi = min(len(prof), int(round(e)) + 6)
        if hi - lo < 3:
            return None, 0.0
        i2 = lo + int(np.argmax(prof[lo:hi]))
        y = float(i2)
        if 0 < i2 < len(prof) - 1:                  # parabolic sub-pixel refine
            d = prof[i2 - 1] - 2 * prof[i2] + prof[i2 + 1]
            if d < 0:
                y += 0.5 * (prof[i2 - 1] - prof[i2 + 1]) / d
        line_ys.append(y)
    quality = float(np.interp(line_ys, rows, prof).min() / prof.max())
    return line_ys, quality


MAX_RESIDUAL = 2.0     # spec: line peaks within +/-2 px of target + k*gap
MAX_INTERP = 0.75      # more interpolated windows than this -> untrustworthy
MAX_DRIFT = 4.0        # fitted staff centre may sit this far off the strip anchor
MIN_QUALITY = 0.40     # weakest line peak / profile max below this = no real staff
RETRACK_GAP = 1.5      # re-track when the fitted gap disagrees with the seed by this


def _fit_comb(line_ys):
    """Least-squares fit line_ys ~ target + k*gap (k = -2..2). Returns
    (target, gap, max_residual)."""
    ks = np.arange(-2, 3)
    ys = np.array(line_ys)
    target = float(ys.mean())
    gap = float((ks * (ys - target)).sum() / (ks ** 2).sum())
    max_res = float(np.abs(ys - (target + ks * gap)).max())
    return target, gap, max_res


def process_system(gray, ink, band, idx):
    """Straighten one system and measure its geometry. Returns
    (strip, sysrec); strip is None only when tracking found no staff at all.

    The persisted (target, gap) are least-squares FITTED to the 5 line peaks
    measured on the straightened strip -- the seed geometry from the raw band
    is only good to a few px (line thickness bias; beams can fuse
    estimate_gap's clusters), and the +/-2px sanity check must run against
    exactly the comb later stages will use. When the fitted gap disagrees
    with the seed by more than RETRACK_GAP the comb tracking itself ran with
    bad spacing, so it is re-run once with the fitted gap."""
    a, b = band
    x0, x1 = staff_x_extent(ink, band)
    gap0 = estimate_gap(ink, band, x0, x1)
    rec = dict(idx=idx, band=[int(a), int(b)], x=[x0, x1],
               gap_raw=round(gap0, 2), flagged=False, reason="")

    for attempt in range(2):
        tracked = track_centers(ink, band, gap0)
        if tracked is None:
            rec.update(flagged=True, reason="no window had enough ink to track")
            return None, rec
        win_x, centers, interp_frac = tracked
        strip, anchor = straighten(gray, band, win_x, centers)
        _, strip_ink = to_ink(strip)
        line_ys, quality = measure_lines(strip_ink, x0, x1, anchor)
        if line_ys is None or abs(_fit_comb(line_ys)[1] - gap0) <= RETRACK_GAP:
            break
        gap0 = _fit_comb(line_ys)[1]        # re-track with the measured gap
    rec["gap_seed"] = round(gap0, 2)

    reasons = []
    target, gap = float(anchor), gap0
    if line_ys is None:
        reasons.append("line peaks unmeasurable")
    else:
        target, gap, max_res = _fit_comb(line_ys)
        rec["max_residual"] = round(max_res, 2)
        rec["line_quality"] = round(quality, 3)
        if max_res > MAX_RESIDUAL:
            reasons.append(f"line peak off by {max_res:.1f}px (>+/-{MAX_RESIDUAL})")
        if abs(target - anchor) > MAX_DRIFT:
            reasons.append(f"staff centre {target - anchor:+.1f}px off strip anchor")
        if quality < MIN_QUALITY:
            reasons.append(f"weak line peaks (quality {quality:.2f})")
    if interp_frac > MAX_INTERP:
        reasons.append(f"{interp_frac:.0%} of track windows interpolated")

    rec.update(target=round(target, 2), gap=round(gap, 3),
               line_ys=[round(y, 2) for y in (line_ys or [])],
               interp_frac=round(interp_frac, 3),
               warp=dict(win_x=[float(x) for x in win_x],
                         center_y=[round(float(c), 2) for c in centers]),
               flagged=bool(reasons), reason="; ".join(reasons))
    return strip, rec


# ---------------------------------------------------------------------------
# Debug overlay: red staff lines + green space/ledger dashes (spec stage 4
# annotation style; here it lets a human verify the geometry at a glance).
# ---------------------------------------------------------------------------
def draw_overlay(strip, target, gap, x0, x1):
    vis = cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR)
    for k in range(-2, 3):                            # the 5 staff lines
        y = int(round(target + k * gap))
        cv2.line(vis, (x0, y), (x1, y), (0, 0, 255), 1)
    for k2 in range(-7, 8, 2):                        # spaces + first ledgers
        y = int(round(target + k2 * gap / 2))
        for x in range(x0, x1, 24):
            cv2.line(vis, (x, y), (x + 10, y), (0, 180, 0), 1)
    return vis


# ---------------------------------------------------------------------------
# Stage 1 driver.
# ---------------------------------------------------------------------------
def process_crop(path, out_root, debug=False):
    """Run stage 1 on one tune crop. Returns the stage1 record (also written
    to data/melody/debug/<id>/stage1.json)."""
    tune_id = os.path.splitext(os.path.basename(path))[0]
    gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        log(f"  {tune_id}: could not read image")
        return None
    g, ink = to_ink(gray)
    bands = staff_bands(ink)
    out_dir = os.path.join(out_root, tune_id)
    os.makedirs(out_dir, exist_ok=True)

    systems = []
    for i, band in enumerate(bands):
        strip, rec = process_system(g, ink, band, i)
        if strip is not None:
            rec["strip"] = f"strip_{i:02d}.png"
            write_png_1bit(os.path.join(out_dir, rec["strip"]), strip)
            if debug:
                vis = draw_overlay(strip, rec["target"], rec["gap"],
                                   rec["x"][0], rec["x"][1])
                write_png(os.path.join(out_dir, f"overlay_{i:02d}.png"), vis)
        systems.append(rec)

    nflag = sum(1 for s in systems if s["flagged"])
    result = dict(source=path.replace("\\", "/"), tune_id=tune_id,
                  n_systems=len(systems), n_flagged=nflag, systems=systems)
    with open(os.path.join(out_dir, "stage1.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    flags = "".join(f"\n      sys {s['idx']}: {s['reason']}"
                    for s in systems if s["flagged"])
    log(f"  {tune_id}: {len(systems)} system(s), {nflag} flagged{flags}")
    return result


def collect_inputs(inputs):
    """Positional args -> list of crop PNGs (files, globs, or directories)."""
    out = []
    for spec in inputs:
        if os.path.isdir(spec):
            out += sorted(glob.glob(os.path.join(spec, "*.png")))
        elif any(ch in spec for ch in "*?["):
            out += sorted(glob.glob(spec))
        else:
            out.append(spec)
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Melody stage 1: staff detection & per-system straightening.")
    ap.add_argument("inputs", nargs="+",
                    help="crop PNG(s), glob(s), or a directory (data/melody/01_crops)")
    ap.add_argument("--out", default="data/melody/debug",
                    help="output root; strips + stage1.json go to <out>/<id>/")
    ap.add_argument("--limit", type=int, default=0,
                    help="process at most N crops (0 = all)")
    ap.add_argument("--debug", action="store_true",
                    help="also write overlay_NN.png with the staff geometry drawn")
    args = ap.parse_args()

    files = collect_inputs(args.inputs)
    if args.limit:
        files = files[:args.limit]
    log(f"melody_straightener (stage 1): {len(files)} crop(s)")
    n_sys = n_flag = n_tunes = 0
    for path in files:
        r = process_crop(path, args.out, debug=args.debug)
        if r:
            n_tunes += 1
            n_sys += r["n_systems"]
            n_flag += r["n_flagged"]
    log(f"DONE  {n_tunes} tune(s), {n_sys} system(s), {n_flag} flagged "
        f"({(n_flag / n_sys * 100) if n_sys else 0:.1f}%)")


if __name__ == "__main__":
    main()
