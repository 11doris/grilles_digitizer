"""Deterministic, pre-read image cleaning for each crop.

Per the spec: optional single binary dilation of the black ink (handwriting reads
better when strokes connect), then downscale the long edge to the minimum still
legible, in grayscale. Never upscale, never super-resolve.
"""

from __future__ import annotations

import base64
from pathlib import Path

import cv2
import numpy as np


def _thicken_ink(gray: np.ndarray, iterations: int) -> np.ndarray:
    """Grow the black (ink) pixels by `iterations` of binary dilation.

    Ink is dark on a light background, so dilating the ink == eroding the
    grayscale image (the local-minimum filter expands dark regions).
    """
    if iterations <= 0:
        return gray
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.erode(gray, kernel, iterations=iterations)


def _downscale(gray: np.ndarray, max_long_edge: int) -> np.ndarray:
    h, w = gray.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_long_edge:
        return gray  # never upscale
    scale = max_long_edge / long_edge
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return cv2.resize(gray, new_size, interpolation=cv2.INTER_AREA)


def prepare_crop(path: Path, *, dilate: int, max_long_edge: int) -> tuple[str, str]:
    """Load a crop, clean it, and return (base64_png, media_type).

    Streams a single image: opened, processed, encoded, released.
    """
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"could not read image: {path}")

    image = _thicken_ink(image, dilate)
    image = _downscale(image, max_long_edge)

    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError(f"could not encode image: {path}")

    return base64.standard_b64encode(buf.tobytes()).decode("ascii"), "image/png"
