#!/usr/bin/env python3
"""Synthesize the swing-brush drum samples for the displayer's practice player.

Renders every one-shot (brush taps, accented taps, hi-hat foot chicks) and the
seamless snare-sweep loop as 44.1 kHz mono WAVs, then embeds them base64-encoded
in apps/displayer/data/brush_samples.js so the fully static app can load them
through a <script> tag (fetch() of local files is blocked on file://).

The sounds are pure noise shaping: white noise spectrally colored with
log-frequency gaussian bands (FFT multiply), then amplitude-enveloped. Because
the spectral multiply is circular over the buffer, the sweep texture is
seamless as a loop by construction; its slow band-motion LFOs run an integer
number of cycles per loop for the same reason. The player (brushes.js) supplies
the tempo-synced swell envelope at runtime, so one texture serves every tempo.

Deterministic (fixed seed): rerunning reproduces the committed file.

Usage:  python apps/displayer/render_brush_samples.py
"""

from __future__ import annotations

import base64
import io
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent / "data" / "brush_samples.js"


def shaped_noise(rng: np.random.Generator, dur: float,
                 bands: list[tuple[float, float, float]],
                 tilt: float = 0.0) -> np.ndarray:
    """White noise colored by gaussian bands in log-frequency.

    bands: (center_hz, bandwidth_octaves, gain). tilt: spectral slope as an
    exponent on f/1kHz (negative = darker). Peak-normalized.
    """
    n = int(round(dur * SR))
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0 / SR)
    shape = np.zeros_like(f)
    safe_f = np.maximum(f, 1.0)
    for fc, octaves, gain in bands:
        shape += gain * np.exp(-0.5 * (np.log2(safe_f / fc) / octaves) ** 2)
    if tilt:
        shape *= (np.maximum(f, 20.0) / 1000.0) ** tilt
    shape[0] = 0.0  # no DC
    y = np.fft.irfft(spectrum * shape, n)
    return y / (np.abs(y).max() + 1e-12)


def envelope(n: int, attack: float, tau: float) -> np.ndarray:
    """Linear attack, exponential decay (time constants in seconds)."""
    t = np.arange(n) / SR
    return np.minimum(t / max(attack, 1e-4), 1.0) * np.exp(-np.maximum(t - attack, 0.0) / tau)


def finish(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    """Peak-normalize and fade the last 8 ms to zero (no end click)."""
    x = x / (np.abs(x).max() + 1e-12) * peak
    fade = min(int(0.008 * SR), len(x))
    x[-fade:] *= np.linspace(1.0, 0.0, fade)
    return x


def brush_tap(rng: np.random.Generator) -> np.ndarray:
    """Soft ride-pattern tap: wire hiss over a faint head thump."""
    dur = 0.22
    fc = rng.uniform(0.92, 1.08)          # per-variant timbre drift
    tau = rng.uniform(0.9, 1.15)
    hiss = (shaped_noise(rng, dur, [(3500 * fc, 0.9, 1.0), (5600 * fc, 0.6, 0.5)])
            * envelope(int(dur * SR), 0.006, 0.045 * tau))
    body = (shaped_noise(rng, dur, [(760 * fc, 0.7, 1.0)])
            * envelope(int(dur * SR), 0.003, 0.030) * 0.32)
    return finish(hiss + body)


def brush_accent(rng: np.random.Generator) -> np.ndarray:
    """Accented tap (beats 2 & 4): brighter snap, longer wire decay."""
    dur = 0.30
    fc = rng.uniform(0.95, 1.05)
    n = int(dur * SR)
    hiss = (shaped_noise(rng, dur, [(3200 * fc, 1.0, 1.0), (6500 * fc, 0.6, 0.6)])
            * envelope(n, 0.005, 0.070))
    snap = (shaped_noise(rng, dur, [(2100 * fc, 1.2, 1.0)])
            * envelope(n, 0.002, 0.012) * 0.5)
    body = (shaped_noise(rng, dur, [(700 * fc, 0.7, 1.0)])
            * envelope(n, 0.003, 0.040) * 0.30)
    return finish(hiss + snap + body)


def hat_chick(rng: np.random.Generator) -> np.ndarray:
    """Foot hi-hat on 2 & 4: short damped metallic 'tsick'."""
    dur = 0.15
    fc = rng.uniform(0.95, 1.05)
    n = int(dur * SR)
    metal = (shaped_noise(rng, dur, [(7400 * fc, 0.5, 1.0), (9600 * fc, 0.4, 0.6)])
             * envelope(n, 0.001, 0.018))
    thump = (shaped_noise(rng, dur, [(310, 0.6, 1.0)])
             * envelope(n, 0.002, 0.025) * 0.28)
    return finish(metal + thump)


def sweep_texture(rng: np.random.Generator) -> np.ndarray:
    """Seamless sustained brush-on-snare swish; runtime gain does the swells.

    Two mid-band layers crossfaded by slow LFOs (2 and 3 whole cycles per loop
    -> seamless) imitate the changing wire contact of a circular sweep.
    """
    dur = 1.8
    n = int(dur * SR)
    t = np.arange(n) / n
    a = shaped_noise(rng, dur, [(1700, 1.2, 1.0)], tilt=-0.3)
    b = shaped_noise(rng, dur, [(3100, 1.1, 1.0)], tilt=-0.2)
    lfo_a = 0.62 + 0.38 * np.sin(2 * np.pi * 2 * t) ** 2
    lfo_b = 0.62 + 0.38 * np.cos(2 * np.pi * 3 * t) ** 2
    x = a * lfo_a * 0.75 + b * lfo_b * 0.55
    return x / (np.abs(x).max() + 1e-12) * 0.9  # no end fade: it loops


def wav_b64(x: np.ndarray) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> None:
    rng = np.random.default_rng(20260711)
    samples = {
        "taps": [wav_b64(brush_tap(rng)) for _ in range(4)],
        "accents": [wav_b64(brush_accent(rng)) for _ in range(2)],
        "hats": [wav_b64(hat_chick(rng)) for _ in range(2)],
        "swish": wav_b64(sweep_texture(rng)),
    }
    parts = ["/* Generated by apps/displayer/render_brush_samples.py — do not edit. */",
             '"use strict";',
             "window.BRUSH_SAMPLES = {",
             f"  rate: {SR},"]
    for key in ("taps", "accents", "hats"):
        parts.append(f"  {key}: [")
        parts.extend(f'    "{b64}",' for b64 in samples[key])
        parts.append("  ],")
    parts.append(f'  swish: "{samples["swish"]}",')
    parts.append("};")
    OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
