#!/usr/bin/env python3
"""
Produce a chords-only copy of apps/displayer for the GitHub Pages deploy.

The displayer is fully data-driven for melody: the Melody tab only appears when
a tune carries melody data (app.js updateTabs: hasChord && hasMelody), melody
panels only render when melody_image/abc exist, and the list icon is gated the
same way. So the "no melody" variant is just the full site with the melody data
filtered out — no code fork.

This script copies a source displayer tree into an output tree and there:
  * drops the melody_crops/ directory (the melody scans),
  * rewrites data/tunes_data.js to strip every melody field
    (melody_image, melody_images, has_melody_abc, abc) and drop tunes that
    have no chord scan (melody-only rows would become empty cards),
  * filters data/similar_data.js so no suggestion points at a dropped tune,
  * removes the Melody tab button from index.html.

The source tree (apps/displayer/) is never modified; Cloudflare keeps deploying
it verbatim (full site). Only GitHub Pages consumes this output.

Usage
-----
    python apps/displayer/build_chords_only.py --out build/pages
    python apps/displayer/build_chords_only.py --src apps/displayer --out <dir>
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

MELODY_FIELDS = ("melody_image", "melody_images", "has_melody_abc", "abc")


def _unwrap(js: str, var: str) -> object:
    """Parse `window.<var> = <json>;` (as emitted by build_data.py)."""
    m = re.match(rf"\s*window\.{var}\s*=\s*(.*);\s*$", js, re.DOTALL)
    if not m:
        raise ValueError(f"unexpected shape for window.{var}")
    return json.loads(m.group(1))


def _emit(var: str, payload: object) -> str:
    """Re-emit minified, matching build_data.py's separators."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"window.{var} = {body};\n"


def strip_tunes(js: str) -> tuple[str, set[str]]:
    """Return (rewritten tunes_data.js, ids kept)."""
    tunes = _unwrap(js, "TUNES")
    kept = []
    for t in tunes:
        if "chord_image" not in t:  # melody-only row -> nothing to show
            continue
        for f in MELODY_FIELDS:
            t.pop(f, None)
        kept.append(t)
    return _emit("TUNES", kept), {t["id"] for t in kept}


def strip_similar(js: str, kept: set[str]) -> str:
    """Drop suggestion entries/targets that reference a removed tune."""
    similar = _unwrap(js, "SIMILAR")
    out = {}
    for tid, suggestions in similar.items():
        if tid not in kept:
            continue
        pruned = [s for s in suggestions
                  if (s.get("id") if isinstance(s, dict) else s) in kept]
        out[tid] = pruned
    return _emit("SIMILAR", out)


def strip_index(html: str) -> str:
    """Remove the Melody tab button (data-gated already, removed for good)."""
    return re.sub(r'[ \t]*<button id="tabMelody".*?</button>\n', "", html,
                  flags=re.DOTALL)


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=str(here),
                        help="Source displayer tree (default: this dir)")
    parser.add_argument("--out", required=True,
                        help="Output tree to create (overwritten if it exists)")
    args = parser.parse_args()

    src = Path(args.src).resolve()
    out = Path(args.out).resolve()
    if out == src:
        print("ERROR: --out must differ from --src", file=sys.stderr)
        return 1

    # Copy everything except the melody scans and dev-only clutter.
    if out.exists():
        shutil.rmtree(out)
    ignore = shutil.ignore_patterns(
        "melody_crops", "__pycache__", "tests", "build_*.py",
        "render_brush_samples.py", "*.pyc")
    shutil.copytree(src, out, ignore=ignore)

    tunes_js = out / "data" / "tunes_data.js"
    rewritten, kept = strip_tunes(tunes_js.read_text(encoding="utf-8"))
    tunes_js.write_text(rewritten, encoding="utf-8")

    similar_js = out / "data" / "similar_data.js"
    if similar_js.is_file():
        similar_js.write_text(
            strip_similar(similar_js.read_text(encoding="utf-8"), kept),
            encoding="utf-8")

    index_html = out / "index.html"
    index_html.write_text(strip_index(index_html.read_text(encoding="utf-8")),
                          encoding="utf-8")

    n_src = len(_unwrap((src / "data" / "tunes_data.js").read_text("utf-8"),
                        "TUNES"))
    print(f"Chords-only build at {out}: kept {len(kept)}/{n_src} tunes, "
          f"melody stripped, melody_crops/ excluded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
