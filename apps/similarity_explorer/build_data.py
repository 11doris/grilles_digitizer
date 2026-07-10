#!/usr/bin/env python3
"""
Bundle similarity output + normalized grids into data/explorer_data.js
(tune_similarity_spec §7).

Reads data/chords/06_similarity/tunes/*.json (full per-tune data), the
matching 05_annotated tunes (for the 2-slot grids, roman numerals and
fingerprint captions) and data/chords/eval/similarity_groundtruth.json
(candidate entries for confirmation mode). The page is static and opens
from file:// — data ships as a .js file, like the displayer's tunes_data.js.

Usage
-----
    python apps/similarity_explorer/build_data.py
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from pipelines.chords.similarity import corpus  # noqa: E402
from pipelines.chords.similarity.normalize import (  # noqa: E402
    degree_name, expand_tune, pitch_class,
)

OUT = Path(__file__).parent / "data" / "explorer_data.js"


def _grid(doc: dict) -> dict:
    """Per section: bars of 2 display slots {sym, roman[, local_roman]}.

    Roman numerals are relative to the tune's global key; sections with a
    Phase 0 local key additionally carry numerals relative to that local
    key (the §7 "locally in A" rendering).
    """
    key = doc["key"]
    section_keys = doc.get("section_keys") or {}
    # degree_name expects the *tonic*; use it directly (not reference_pc,
    # which maps minors to the relative major for the matching space).
    global_tonic = pitch_class(key["tonic"])
    sections = {}
    start_bar = 0
    for name, slots in expand_tune(doc).items():
        local = section_keys.get(name)
        local_tonic = pitch_class(local["tonic"]) if local else None
        bars: list[dict] = []
        for slot in slots:
            if slot.half == 0:
                bars.append({"bar": slot.bar, "slots": []})
            ch = slot.chord
            cell = {"sym": ch.symbol}
            if ch.is_sounding:
                cell["roman"] = degree_name(ch.root_pc, global_tonic, ch.quality)
                if local_tonic is not None:
                    cell["local_roman"] = degree_name(ch.root_pc, local_tonic,
                                                      ch.quality)
            else:
                cell["roman"] = "N.C."
            bars[-1]["slots"].append(cell)
        sections[name] = {"start_bar": start_bar, "bars": bars,
                          "local_key": local}
        start_bar += len(bars)
    return sections


def build(similarity_dir: Path, annotated_dir: Path, out: Path) -> int:
    tunes_dir = similarity_dir / "tunes"
    if not tunes_dir.exists():
        print(f"error: {tunes_dir} missing — run "
              "`python -m pipelines.chords.similarity.compute` first",
              file=sys.stderr)
        return 1

    docs = corpus.load_corpus(annotated_dir)
    bundle_tunes = {}
    for path in sorted(tunes_dir.glob("*.json")):
        sim = json.loads(path.read_text("utf-8"))
        doc = docs.get(path.stem)
        if doc is None:
            continue
        fp = doc.get("harmonic_fingerprint") or {}
        bundle_tunes[path.stem] = {
            **sim,
            "meter": doc.get("time_signature"),
            "mode": doc["key"]["mode"],
            "fingerprint_sections": fp.get("sections") or {},
            "tags": fp.get("tags") or [],
            "grid": _grid(doc),
        }

    gt_path = corpus.EVAL_DIR / "similarity_groundtruth.json"
    groundtruth = (json.loads(gt_path.read_text("utf-8"))
                   if gt_path.exists() else {"families": [], "non_matches": []})

    index_path = similarity_dir / "index.json"
    index = json.loads(index_path.read_text("utf-8")) if index_path.exists() else {}

    bundle = {
        "built": datetime.datetime.now().isoformat(timespec="seconds"),
        "engine": {k: index.get(k) for k in
                   ("built", "engine_version", "corpus", "sections")},
        "tunes": bundle_tunes,
        "groundtruth": groundtruth,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("window.EXPLORER_DATA = "
                   + json.dumps(bundle, ensure_ascii=False) + ";\n", "utf-8")
    print(f"{len(bundle_tunes)} tunes -> {out}"
          f" ({out.stat().st_size / 1e6:.2f} MB)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--similarity", default=str(corpus.SIMILARITY_DIR))
    parser.add_argument("--annotated", default=str(corpus.ANNOTATED_DIR))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    return build(Path(args.similarity), Path(args.annotated), Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
