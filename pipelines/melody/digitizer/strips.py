"""Staff-band detection and per-system overlay strips (plan §1, demo-proven).

The overlays follow the hand-drawn staff curvature window by window and draw
a pitch ruler over the ink: RED on the five staff lines, GREEN dashes on the
spaces plus one position above/below (G5, D4), BLUE dashes on the first
ledger positions (A5 above, C4 below). The demo showed these make pitch
reading tractable for the VLM's second pass (decorrelated evidence).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

RED = (255, 40, 40)
GREEN = (0, 150, 0)
BLUE = (40, 80, 255)

INK_THRESHOLD = 128
BAND_MIN_FRAC = 0.45  # a staff row is darker than this fraction of the max
BAND_JOIN_GAP = 40  # rows closer than this belong to one staff band
WINDOW_STEP = 20  # comb-fit window stride (px)
WINDOW_WIDTH = 40
CONTINUITY = 6  # max comb-center drift between neighboring windows (px)
MARGIN_ABOVE = 120  # strip margin above the top line (ledger space)
MARGIN_BELOW = 150


@dataclass(frozen=True)
class SystemTrack:
    """One staff system: absolute crop coordinates + the fitted comb track."""

    index: int  # 1-based system number, top to bottom
    top: int  # strip top edge (absolute y in the crop)
    bot: int  # strip bottom edge
    gap: float  # staff line spacing
    xs: tuple[int, ...]  # window left edges
    centers: tuple[float, ...]  # top-line y per window, strip-local

    def center_at(self, x: float) -> float:
        """Strip-local top-line y at crop x (nearest window)."""
        j = min(range(len(self.xs)),
                key=lambda j: abs(self.xs[j] + WINDOW_STEP - x))
        return self.centers[j]

    def line_ys(self, x: float) -> list[float]:
        """Absolute y of the five staff lines (F5..E4, top to bottom) at x."""
        c = self.center_at(x)
        return [self.top + c + k * self.gap for k in range(5)]


def load_ink(png_path: Path) -> tuple[Image.Image, np.ndarray]:
    img = Image.open(png_path).convert("L")
    return img, np.asarray(img) < INK_THRESHOLD


def find_bands(ink: np.ndarray) -> list[tuple[int, int]]:
    """(y0, y1) row bands of the staff systems, top to bottom."""
    dark = ink.sum(axis=1)
    rows = np.where(dark > BAND_MIN_FRAC * dark.max())[0]
    if len(rows) == 0:
        return []
    bands = []
    start = prev = int(rows[0])
    for y in rows[1:]:
        if y - prev > BAND_JOIN_GAP:
            bands.append((start, prev))
            start = int(y)
        prev = int(y)
    bands.append((start, prev))
    return bands


def _fit_comb(prof: np.ndarray, offsets: np.ndarray, lo: float, hi: float,
              sh: int) -> tuple[float, float]:
    best, center = -1.0, (lo + hi) / 2
    for c in np.arange(lo, hi, 0.5):
        idx = np.clip((c + offsets).astype(int), 0, sh - 1)
        s = float(prof[idx].sum())
        if s > best:
            best, center = s, float(c)
    return best, center


def build_track(ink: np.ndarray, band: tuple[int, int], index: int) -> SystemTrack:
    """Per-window 5-line comb fit with best-anchor + continuity propagation."""
    h, w = ink.shape
    y0, y1 = band
    gap = (y1 - y0) / 4.0
    top = max(0, y0 - MARGIN_ABOVE)
    bot = min(h, y1 + MARGIN_BELOW)
    sh = bot - top
    cy0 = y0 - top
    offsets = np.arange(5) * gap
    xs = list(range(0, w, WINDOW_STEP))
    profs = [ink[top:bot, x:min(w, x + WINDOW_WIDTH)].sum(axis=1).astype(float)
             for x in xs]

    scores = [_fit_comb(p, offsets, cy0 - 25, cy0 + 25, sh) for p in profs]
    anchor = int(np.argmax([s for s, _ in scores]))
    centers = np.zeros(len(xs))
    centers[anchor] = scores[anchor][1]
    for j in range(anchor + 1, len(xs)):
        centers[j] = _fit_comb(profs[j], offsets, centers[j - 1] - CONTINUITY,
                               centers[j - 1] + CONTINUITY, sh)[1]
    for j in range(anchor - 1, -1, -1):
        centers[j] = _fit_comb(profs[j], offsets, centers[j + 1] - CONTINUITY,
                               centers[j + 1] + CONTINUITY, sh)[1]
    return SystemTrack(index=index, top=top, bot=bot, gap=gap,
                       xs=tuple(xs), centers=tuple(float(c) for c in centers))


def build_tracks(png_path: Path) -> list[SystemTrack]:
    _, ink = load_ink(png_path)
    return [build_track(ink, band, i)
            for i, band in enumerate(find_bands(ink), 1)]


def render_overlays(png_path: Path, out_dir: Path) -> list[Path]:
    """Write ov<N>_full.png plus 2× L/R halves per system; return the paths."""
    img, ink = load_ink(png_path)
    w = img.width
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for track in build_tracks(png_path):
        strip = np.array(img.crop((0, track.top, w, track.bot)).convert("RGB"))
        sh = strip.shape[0]
        gap = track.gap
        for j, x in enumerate(track.xs):
            x1 = min(w, x + WINDOW_WIDTH)
            center = track.centers[j]
            for k in range(5):
                yy = int(round(center + k * gap))
                if 0 <= yy < sh:
                    strip[yy, x:x1] = RED
            if j % 2 == 0:
                for k in (-1, 1, 3, 5, 7, 9):
                    yy = int(round(center + k * gap / 2))
                    if 0 <= yy < sh:
                        strip[yy, x:x1] = GREEN
                for k in (-2, 10):
                    yy = int(round(center + k * gap / 2))
                    if 0 <= yy < sh:
                        strip[yy, x:x1] = BLUE
        out = Image.fromarray(strip)
        full = out_dir / f"ov{track.index}_full.png"
        out.save(full)
        paths.append(full)
        for name, (xa, xb) in (("L", (0, w // 2 + 60)), ("R", (w // 2 - 60, w))):
            part = out.crop((xa, 0, xb, sh))
            part = part.resize((part.width * 2, part.height * 2), Image.LANCZOS)
            p = out_dir / f"ov{track.index}_{name}.png"
            part.save(p)
            paths.append(p)
    return paths


def zoom_crop(overlay_dir: Path, system: int, x0: int, x1: int,
              scale: float = 3.0) -> Path:
    """Zoom a region of an overlay strip; x in original crop pixels."""
    src = overlay_dir / f"ov{system}_full.png"
    img = Image.open(src)
    crop = img.crop((x0, 0, x1, img.height))
    crop = crop.resize((int(crop.width * scale), int(crop.height * scale)),
                       Image.LANCZOS)
    p = overlay_dir / f"zoom_s{system}_{x0}_{x1}.png"
    crop.save(p)
    return p
