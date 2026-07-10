#!/usr/bin/env python3
"""
Tune verification web app.

Usage
-----
    python apps/verifier/verify_app.py
    python apps/verifier/verify_app.py --tunes data/chords/02_raw --crops data/chords/01_crops --port 5000

Verified tunes and WIP edits go to the siblings of --tunes: <parent>/verified
and <parent>/wip (data/chords/04_verified and data/chords/03_wip by default).

Each tune is in one of three review states, tracked in verification_state.json:
verified, deferred (parked for a later pass), or needs_review (neither).
Verified and deferred are mutually exclusive.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

# ---------------------------------------------------------------------------
# Mutable globals — overridden by CLI args before app.run()
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[2]  # repo root
sys.path.insert(0, str(_REPO))

from pipelines.chords.digitizer.validation import (  # noqa: E402
    ValidationError, _check_chord, _check_section_keys, _iter_chords)
from pipelines.chords.similarity.normalize import (  # noqa: E402
    ChordParseError, expand_tune)
TUNES_DIR = (_REPO / "data" / "chords" / "02_raw").resolve()
CROPS_DIR = (_REPO / "data" / "chords" / "01_crops").resolve()
VERIFIED_DIR = (_REPO / "data" / "chords" / "04_verified").resolve()
# Edits are never written back to TUNES_DIR (read-only source). They live in
# WIP_DIR until a tune is verified, at which point it is copied to VERIFIED_DIR.
WIP_DIR = (_REPO / "data" / "chords" / "03_wip").resolve()

_IGNORED_STEMS = frozenset(
    {"run_report", "run_state", "verification_state", "batch_state"})

app = Flask(__name__, template_folder="templates", static_folder="static")
# Preserve insertion order of JSON keys (e.g. section names) instead of
# sorting them alphabetically, which Flask's JSON provider does by default.
app.json.sort_keys = False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    # State lives in the chords root (a tracked directory), separate from the
    # gitignored WIP edits, so the review state can be versioned.
    return WIP_DIR.parent / "verification_state.json"


def _wip_path(tune_id: str) -> Path:
    return WIP_DIR / f"{tune_id}.json"


def _source_path(tune_id: str) -> Path | None:
    """Path to read a tune from: the WIP edit if present, else the source.

    Returns None if the tune does not exist in either location.
    """
    wp = _wip_path(tune_id)
    if wp.exists():
        return wp
    sp = TUNES_DIR / f"{tune_id}.json"
    return sp if sp.exists() else None


def _is_tune(p: Path) -> bool:
    return (
        p.is_file()
        and p.suffix == ".json"
        and not p.name.endswith(".error.json")  # transcription failure stubs
        and not p.stem.endswith("_opus")
        and p.stem not in _IGNORED_STEMS
    )


def _validate_tune(data: dict) -> str | None:
    """Why this tune would break downstream builds, or None if it is clean.

    Runs the digitizer's structural checks plus the similarity engine's chord
    parser, so a bad edit fails here — in front of the reviewer — instead of
    aborting a later similarity/displayer build (tune_similarity_spec §10).
    """
    sections = data.get("sections")
    if not isinstance(sections, dict):
        return "sections must be an object"
    try:
        for chord in _iter_chords(sections):
            _check_chord(chord)
        _check_section_keys(sections)
        expand_tune(data)
    except (ValidationError, ChordParseError) as exc:
        return str(exc)
    return None


# Title cache for the list endpoint, keyed by source file and mtime: a list
# refresh over the full book (~1600 tunes) must not re-parse every JSON.
_title_cache: dict[Path, tuple[int, str]] = {}


def _tune_title(tid: str, fallback: Path) -> str:
    src = _source_path(tid) or fallback
    try:
        mtime = src.stat().st_mtime_ns
    except OSError:
        return tid
    cached = _title_cache.get(src)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        title = json.loads(src.read_text("utf-8")).get("title", tid)
    except Exception:
        title = tid
    _title_cache[src] = (mtime, title)
    return title


def _tune_sort_key(p: Path) -> tuple:
    """Natural sort: numeric prefix first, then full name."""
    m = re.match(r'^(\d+)', p.name)
    return (int(m.group(1)) if m else 0, p.name)


def _list_tunes() -> list[Path]:
    return sorted(
        (p for p in TUNES_DIR.iterdir() if _is_tune(p)),
        key=_tune_sort_key,
    )


def _load_state() -> dict:
    sp = _state_path()
    if sp.exists():
        try:
            return json.loads(sp.read_text("utf-8"))
        except Exception:
            pass
    return {
        "last_opened": None,
        "verified": [],
        "deferred": [],
        "in_progress": None,
    }


def _save_state(s: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(
        json.dumps(s, indent=2, ensure_ascii=False), "utf-8"
    )


def _safe_id(tid: str) -> bool:
    """Reject IDs that could traverse directories."""
    return (
        bool(tid)
        and "/" not in tid
        and "\\" not in tid
        and ".." not in tid
    )


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
    state = _load_state()
    verified_set = set(state.get("verified", []))
    deferred_set = set(state.get("deferred", []))
    tunes = []
    for p in _list_tunes():
        tid = p.stem
        # Show the WIP title if the tune has unsaved-to-source edits.
        title = _tune_title(tid, p)
        if tid in verified_set:
            status = "verified"
        elif tid in deferred_set:
            status = "deferred"
        else:
            status = "needs_review"
        tunes.append({
            "id": tid,
            "title": title,
            "verified": status == "verified",
            "deferred": status == "deferred",
            "status": status,
            "has_image": (CROPS_DIR / f"{tid}.png").exists(),
        })
    counts = {s: sum(1 for t in tunes if t["status"] == s)
              for s in ("verified", "deferred", "needs_review")}
    return jsonify({
        "tunes": tunes,
        "total": len(tunes),
        "counts": counts,
        "verified": counts["verified"],
        "deferred": counts["deferred"],
        "remaining": len(tunes) - counts["verified"],
        "last_opened": state.get("last_opened"),
        "in_progress": state.get("in_progress"),
    })


@app.route("/api/tunes/<tune_id>")
def api_get(tune_id: str):
    if not _safe_id(tune_id):
        abort(400)
    src = _source_path(tune_id)
    if src is None:
        abort(404)
    state = _load_state()
    data = json.loads(src.read_text("utf-8"))
    return jsonify({
        "id": tune_id,
        "verified": tune_id in state.get("verified", []),
        "deferred": tune_id in state.get("deferred", []),
        "data": data,
    })


@app.route("/api/tunes/<tune_id>", methods=["PUT"])
def api_save(tune_id: str):
    if not _safe_id(tune_id):
        abort(400)
    # The tune must exist as a source (or an existing WIP edit); we never
    # create tunes out of thin air, but we also never write back to TUNES_DIR.
    if _source_path(tune_id) is None:
        abort(404)
    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"error": "Invalid JSON body"}), 400
    error = _validate_tune(body)
    if error:
        return jsonify({"error": f"Validation failed: {error}"}), 400
    WIP_DIR.mkdir(exist_ok=True)
    _wip_path(tune_id).write_text(
        json.dumps(body, indent=2, ensure_ascii=False), "utf-8"
    )
    s = _load_state()
    if s.get("in_progress") == tune_id:
        s["in_progress"] = None
        _save_state(s)
    return jsonify({"ok": True})


@app.route("/api/tunes/<tune_id>/verify", methods=["POST"])
def api_verify(tune_id: str):
    if not _safe_id(tune_id):
        abort(400)
    src = _source_path(tune_id)
    if src is None:
        abort(404)
    # Promotion gate: 04_verified is ground truth for every downstream stage,
    # so nothing unparseable may ever land there.
    try:
        data = json.loads(src.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"Cannot read tune: {exc}"}), 400
    error = _validate_tune(data)
    if error:
        return jsonify({"error": f"Not verified — fix first: {error}"}), 400
    VERIFIED_DIR.mkdir(exist_ok=True)
    shutil.copy2(src, VERIFIED_DIR / f"{tune_id}.json")
    s = _load_state()
    verified = s.get("verified", [])
    if tune_id not in verified:
        verified.append(tune_id)
    s["verified"] = verified
    # Verified is exclusive with the other review states.
    s["deferred"] = [d for d in s.get("deferred", []) if d != tune_id]
    _save_state(s)
    return jsonify({"ok": True})


@app.route("/api/tunes/<tune_id>/verify", methods=["DELETE"])
def api_unverify(tune_id: str):
    if not _safe_id(tune_id):
        abort(400)
    s = _load_state()
    s["verified"] = [v for v in s.get("verified", []) if v != tune_id]
    _save_state(s)
    vp = VERIFIED_DIR / f"{tune_id}.json"
    if vp.exists():
        vp.unlink()
    return jsonify({"ok": True})


@app.route("/api/tunes/<tune_id>/defer", methods=["POST"])
def api_defer(tune_id: str):
    """Park a tune for a later review pass (mutually exclusive with verified)."""
    if not _safe_id(tune_id):
        abort(400)
    if _source_path(tune_id) is None:
        abort(404)
    s = _load_state()
    deferred = s.get("deferred", [])
    if tune_id not in deferred:
        deferred.append(tune_id)
    s["deferred"] = deferred
    # Deferred is exclusive with verified.
    # A deferred tune is not verified; drop any stale verified file/state.
    if tune_id in s.get("verified", []):
        s["verified"] = [v for v in s["verified"] if v != tune_id]
        vp = VERIFIED_DIR / f"{tune_id}.json"
        if vp.exists():
            vp.unlink()
    _save_state(s)
    return jsonify({"ok": True})


@app.route("/api/tunes/<tune_id>/defer", methods=["DELETE"])
def api_undefer(tune_id: str):
    """Return a deferred tune to the review queue."""
    if not _safe_id(tune_id):
        abort(400)
    s = _load_state()
    s["deferred"] = [d for d in s.get("deferred", []) if d != tune_id]
    _save_state(s)
    return jsonify({"ok": True})


@app.route("/api/state", methods=["PUT"])
def api_state():
    body = request.get_json(silent=True) or {}
    s = _load_state()
    for k in ("last_opened", "in_progress"):
        if k in body:
            s[k] = body[k]
    _save_state(s)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tune verification web app")
    parser.add_argument("--tunes", default=str(_REPO / "data" / "chords" / "02_raw"),
                        help="Path to raw tune JSON directory")
    parser.add_argument("--crops", default=str(_REPO / "data" / "chords" / "01_crops"),
                        help="Path to crops directory")
    parser.add_argument("--port", type=int, default=5000, help="Port (default 5000)")
    args = parser.parse_args()

    TUNES_DIR = Path(args.tunes).resolve()
    CROPS_DIR = Path(args.crops).resolve()
    VERIFIED_DIR = TUNES_DIR.parent / "04_verified"
    WIP_DIR = TUNES_DIR.parent / "03_wip"

    if not TUNES_DIR.exists():
        parser.error(f"Tunes directory not found: {TUNES_DIR}")

    def _open_browser():
        time.sleep(0.9)
        webbrowser.open(f"http://localhost:{args.port}")

    threading.Thread(target=_open_browser, daemon=True).start()
    app.run(debug=False, port=args.port, use_reloader=False)
