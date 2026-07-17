"""Deterministic measurement evidence for contested glyphs (plan §1, §4).

Two probes, both operating on a `strips.SystemTrack`:

* `barlines()` — x positions of staff-spanning vertical ink runs (>= 75 % of
  the staff height). Used to segment bars and anchor zoom crops.
* `heads()` — staff-line-removed connected-component blobs in an x-window,
  each with its ink centroid read against the comb as a diatonic step.

The demo showed blob centroids carry a small per-system systematic offset
(~0.8 step observed once): always calibrate against known heads before
trusting the step readout — `calibrate()` returns the offset to pass back
into `heads()`. Uncalibrated readings are evidence, not verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .strips import SystemTrack

_LETTERS = "CDEFGAB"
_F5_DIATONIC = 5 * 7 + _LETTERS.index("F")  # step 0 of the comb = F5

BARLINE_MIN_COVER = 0.75
BARLINE_JOIN_PX = 6
MIN_BLOB_PX = 120
STAFF_LINE_MAX_THICKNESS = 5


def step_name(step: float) -> str:
    """ABC-ish name for a comb step (0 = F5 line, +1 per half-gap DOWN)."""
    idx = _F5_DIATONIC - round(step)
    if idx < 0:
        return "?"
    letter = _LETTERS[idx % 7]
    octave = idx // 7
    return f"{letter.lower() if octave >= 5 else letter}{octave}"


@dataclass(frozen=True)
class Barline:
    x: int
    cover: float  # fraction of staff height covered by the run
    width: int


def barlines(ink: np.ndarray, track: SystemTrack) -> list[Barline]:
    """Candidate barlines: grouped staff-spanning vertical ink runs."""
    w = ink.shape[1]
    hits: list[tuple[int, float]] = []
    for x in range(w):
        c = track.center_at(x)
        t = int(track.top + c)
        b = int(track.top + c + 4 * track.gap)
        col = ink[max(0, t - 4):b + 5, x]
        best = run = 0
        for v in col:
            run = run + 1 if v else 0
            best = max(best, run)
        if best >= BARLINE_MIN_COVER * (b - t):
            hits.append((x, best / (b - t)))
    groups: list[list[tuple[int, float]]] = []
    for x, f in hits:
        if groups and x - groups[-1][-1][0] <= BARLINE_JOIN_PX:
            groups[-1].append((x, f))
        else:
            groups.append([(x, f)])
    return [
        Barline(
            x=int(np.mean([x for x, _ in g])),
            cover=max(f for _, f in g),
            width=len(g),
        )
        for g in groups
    ]


@dataclass(frozen=True)
class Blob:
    pixels: int
    x0: int  # window-relative
    x1: int
    height: float
    width: float
    step: float  # comb step of the centroid (calibration applied)
    name: str
    kind: str  # 'head?' | 'wide' | 'tall'


def heads(
    ink: np.ndarray,
    track: SystemTrack,
    x0: int,
    x1: int,
    calibration: float = 0.0,
) -> list[Blob]:
    """Staff-line-removed ink blobs in [x0, x1) with step readouts.

    `calibration` (in steps) is subtracted from the raw centroid step; get it
    from `calibrate()` — see the module docstring.
    """
    gap = track.gap
    lys = track.line_ys((x0 + x1) / 2)
    y0 = int(lys[0] - 3.2 * gap)
    y1 = int(lys[4] + 3.2 * gap)
    win = ink[y0:y1, x0:x1].copy()
    # remove staff lines: short vertical runs that contain a line row
    lrows = [int(round(ly - y0)) for ly in lys]
    H, W = win.shape
    for c in range(W):
        r = 0
        while r < H:
            if win[r, c]:
                r2 = r
                while r2 < H and win[r2, c]:
                    r2 += 1
                if (r2 - r) <= STAFF_LINE_MAX_THICKNESS and any(
                        r <= lr < r2 for lr in lrows):
                    win[r:r2, c] = False
                r = r2
            else:
                r += 1
    lab, n = ndimage.label(win)
    blobs: list[Blob] = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if len(ys) < MIN_BLOB_PX:
            continue
        cy = y0 + ys.mean()
        step = (cy - lys[0]) / (gap / 2) - calibration
        hh = float(ys.max() - ys.min())
        ww = float(xs.max() - xs.min())
        kind = ("head?" if (hh <= 1.6 * gap and 10 <= ww <= 60)
                else ("wide" if ww > 60 else "tall"))
        blobs.append(Blob(
            pixels=len(ys), x0=int(xs.min()), x1=int(xs.max()),
            height=hh, width=ww, step=step, name=step_name(step), kind=kind,
        ))
    return blobs


def calibrate(
    ink: np.ndarray,
    track: SystemTrack,
    known: list[tuple[int, int, int]],
) -> float:
    """Per-system centroid offset from reference heads.

    `known` = [(x0, x1, true_step)] windows each containing exactly one
    unambiguous notehead whose comb step is `true_step`. Returns the median
    raw-minus-true offset in steps, to pass as `heads(calibration=...)`.
    """
    offsets: list[float] = []
    for x0, x1, true_step in known:
        cands = [b for b in heads(ink, track, x0, x1) if b.kind == "head?"]
        if len(cands) == 1:
            offsets.append(cands[0].step - true_step)
    if not offsets:
        return 0.0
    return float(np.median(offsets))
