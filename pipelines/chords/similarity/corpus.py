"""Corpus loading shared by the similarity engine and the eval harness."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ANNOTATED_DIR = REPO / "data" / "chords" / "05_annotated"
SIMILARITY_DIR = REPO / "data" / "chords" / "06_similarity"
EVAL_DIR = REPO / "data" / "chords" / "eval"

# needs_review tunes are excluded from similarity output until a human
# resolves them (spec §3.1/§6).
INCLUDED_STATUSES = ("agreed", "verified")


def load_corpus(annotated_dir: Path = ANNOTATED_DIR) -> dict[str, dict]:
    """stem -> annotated tune doc, for tunes included in similarity."""
    out: dict[str, dict] = {}
    for path in sorted(annotated_dir.glob("*.json")):
        doc = json.loads(path.read_text("utf-8"))
        status = (doc.get("key_annotation") or {}).get("status")
        if status in INCLUDED_STATUSES:
            out[path.stem] = doc
    return out
