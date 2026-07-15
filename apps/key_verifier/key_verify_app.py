#!/usr/bin/env python3
"""
Key verification web app (Phase 0, tune_similarity_spec §3.6).

Usage
-----
    python apps/key_verifier/key_verify_app.py \
        [--annotated data/chords/05_annotated] [--crops data/chords/01_crops] [--port 5001]

Walks the annotated tunes (needs_review queue first), showing the original
crop PNG next to the resolved key, both voter votes and the harmonic
fingerprint. Saves write straight back to 05_annotated — this pipeline's own
output, so no WIP tier — through the shared update routine in
pipelines.chords.key_annotation.core, which recomputes the derived fields
(`opening`, `section_keys` consistency) and preserves the voter votes.
Progress is derived from the files' `status` fields; no separate state file.
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

_REPO = Path(__file__).resolve().parents[2]  # repo root
sys.path.insert(0, str(_REPO))

from pipelines.chords.key_annotation import core  # noqa: E402
from pipelines.chords.similarity.normalize import sections_view  # noqa: E402

# Mutable globals — overridden by CLI args before app.run()
ANNOTATED_DIR = (_REPO / "data" / "chords" / "05_annotated").resolve()
CROPS_DIR = (_REPO / "data" / "chords" / "01_crops").resolve()

app = Flask(__name__, template_folder="templates", static_folder="static")
# Preserve insertion order of JSON keys (section names, votes) instead of
# Flask's default alphabetical sort.
app.json.sort_keys = False  # type: ignore[attr-defined]


def _safe_id(tid: str) -> bool:
    return bool(tid) and "/" not in tid and "\\" not in tid and ".." not in tid


def _tune_sort_key(p: Path) -> tuple:
    m = re.match(r"^(\d+)", p.name)
    return (int(m.group(1)) if m else 0, p.name)


def _list_paths() -> list[Path]:
    return sorted(ANNOTATED_DIR.glob("*.json"), key=_tune_sort_key)


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@app.route("/")
def ui_index():
    return render_template("index.html")


@app.route("/crop/<tune_id>")
def ui_crop(tune_id: str):
    if not _safe_id(tune_id):
        abort(400)
    img = CROPS_DIR / f"{tune_id}.png"
    if not img.exists():
        abort(404)
    return send_file(img, mimetype="image/png")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/tunes")
def api_list():
    tunes = []
    for p in _list_paths():
        d = core.read_json(p)
        ann = d.get("key_annotation") or {}
        tunes.append({
            "id": p.stem,
            "title": d.get("title", p.stem),
            "status": ann.get("status", "needs_review"),
            "key": d.get("key"),
            "opening": (d.get("opening") or {}).get("degree"),
            "has_image": (CROPS_DIR / f"{p.stem}.png").exists(),
        })
    counts = {s: sum(1 for t in tunes if t["status"] == s)
              for s in ("verified", "agreed", "needs_review")}
    return jsonify({"tunes": tunes, "counts": counts, "total": len(tunes)})


@app.route("/api/tunes/<tune_id>")
def api_get(tune_id: str):
    if not _safe_id(tune_id):
        abort(400)
    path = ANNOTATED_DIR / f"{tune_id}.json"
    if not path.exists():
        abort(404)
    doc = core.read_json(path)
    # Part ids in document order (strains model) — the client keys its
    # section-keys editor and fingerprint text areas off these, so the id
    # generation stays single-sourced in normalize.sections_view.
    return jsonify({"id": tune_id, "data": doc,
                    "section_ids": list(sections_view(doc))})


@app.route("/api/tunes/<tune_id>/verify", methods=["POST"])
def api_verify(tune_id: str):
    """Verify (accept as shown) or correct-and-verify one tune.

    Body (all fields optional): {"tonic": ..., "mode": ...,
    "section_keys": {name: {tonic, mode}, ...} | null,
    "fingerprint": {family?, sections?, modulates?, modulation_note?}}
    A posted `tags` is ignored — tags are derived from the building blocks
    by the update routine.
    """
    if not _safe_id(tune_id):
        abort(400)
    path = ANNOTATED_DIR / f"{tune_id}.json"
    if not path.exists():
        abort(404)
    body = request.get_json(silent=True) or {}

    kwargs = {}
    if body.get("tonic") is not None:
        kwargs["tonic"] = body["tonic"]
    if body.get("mode") is not None:
        if body["mode"] not in ("major", "minor"):
            return jsonify({"error": "mode must be major or minor"}), 400
        kwargs["mode"] = body["mode"]
    if "section_keys" in body:
        kwargs["section_keys"] = body["section_keys"]
    if "fingerprint" in body:
        kwargs["fingerprint"] = body["fingerprint"]

    annotated = core.read_json(path)
    try:
        core.update_annotation(annotated, **kwargs)
    except (KeyError, ValueError) as exc:
        return jsonify({"error": f"invalid request: {exc}"}), 400
    core.write_annotated(path, annotated)
    return jsonify({"ok": True, "data": annotated})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Key verification web app")
    parser.add_argument("--annotated",
                        default=str(_REPO / "data" / "chords" / "05_annotated"),
                        help="Path to the annotated tunes directory")
    parser.add_argument("--crops",
                        default=str(_REPO / "data" / "chords" / "01_crops"),
                        help="Path to crops directory")
    parser.add_argument("--port", type=int, default=5001, help="Port (default 5001)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open a browser window")
    args = parser.parse_args()

    ANNOTATED_DIR = Path(args.annotated).resolve()
    CROPS_DIR = Path(args.crops).resolve()
    if not ANNOTATED_DIR.exists():
        parser.error(f"Annotated directory not found: {ANNOTATED_DIR} "
                     "(run pipelines/chords/annotate_keys.py first)")

    if not args.no_browser:
        def _open_browser():
            time.sleep(0.9)
            webbrowser.open(f"http://localhost:{args.port}")
        threading.Thread(target=_open_browser, daemon=True).start()

    app.run(debug=True, port=args.port, use_reloader=False)
