#!/usr/bin/env python3
"""Backfill `form_strains` and `section_labels` into the tune JSONs.

Both fields are DERIVED (by pipelines/chords/similarity/normalize.derive_labels)
from a tune's own printed `form` string and its section keys — they are not
model output. They are added, not replacing anything: the verbatim `form`
string stays for provenance.

  form_strains   the printed form split per strain, verse/chorus/sN separated:
                 {"chorus": {"bars": 32, "labels": ["A","A","B","A"]},
                  "verse":  {"bars": 16, "labels": ["A","A"], "source": ...}}
  section_labels {section_key: printed label} with primes kept
                 ({"A":"A","A1":"A'","B":"B","A2":"A"}).

Written into every layer that has the tune (04_verified, 03_wip, 05_annotated),
computed independently from each file's own content. Tunes with a HARD form
mismatch (see check_form.py) still get best-effort labels (key-derived where the
form and sections disagree); they are reported so they can be fixed by hand.

Usage:
    python pipelines/chords/tools/migrate_form_labels.py            # dry run
    python pipelines/chords/tools/migrate_form_labels.py --apply    # write
    python pipelines/chords/tools/migrate_form_labels.py --sample 72_01_CHEROKEE
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root
from pipelines.chords.similarity.normalize import HARD, derive_labels  # noqa: E402

IGNORED_STEMS = {"verification_state", "run_report", "run_state"}
LAYERS = ("04_verified", "03_wip", "05_annotated")


def _reorder(tune: dict, strains: dict, labels: dict) -> dict:
    """Return a new dict with form_strains + section_labels inserted right
    after `form`, preserving every other key's order."""
    out: dict = {}
    for key, val in tune.items():
        if key in ("form_strains", "section_labels"):
            continue  # drop any earlier run's copy; re-inserted below
        out[key] = val
        if key == "form":
            out["form_strains"] = strains
            out["section_labels"] = labels
    if "form" not in tune:  # no form key — append at the end
        out["form_strains"] = strains
        out["section_labels"] = labels
    return out


def _derive(tune: dict) -> tuple[dict, dict, list[str]]:
    structured, labels, warnings = derive_labels(tune)
    structured.pop("printed", None)  # already in `form`; no need to duplicate
    hard = [m for lv, m in warnings if lv == HARD]
    return structured, labels, hard


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    args = sys.argv[1:]
    apply = "--apply" in args
    sample = None
    if "--sample" in args:
        sample = args[args.index("--sample") + 1]
    repo = Path(__file__).resolve().parents[3]
    chords = repo / "data" / "chords"

    stems = sorted(
        p.stem for p in (chords / "04_verified").glob("*.json")
        if p.stem not in IGNORED_STEMS and not p.stem.endswith("_opus"))
    if sample:
        stems = [s for s in stems if s == sample]

    hard_report: list[str] = []
    n_written = 0
    for stem in stems:
        for layer in LAYERS:
            path = chords / layer / f"{stem}.json"
            if not path.is_file():
                continue
            tune = json.loads(path.read_text("utf-8"))
            strains, labels, hard = _derive(tune)
            if layer == "04_verified" and hard:
                hard_report.append(f"{stem}: {hard[0]}")
            out = _reorder(tune, strains, labels)
            if sample or not apply:
                if layer == "04_verified":
                    print(f"--- {stem} ---")
                    print(f"  form_strains:   {json.dumps(strains, ensure_ascii=False)}")
                    print(f"  section_labels: {json.dumps(labels, ensure_ascii=False)}")
                    if hard:
                        print(f"  HARD: {hard[0]}")
                continue
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            n_written += 1

    print()
    if apply and not sample:
        print(f"wrote {n_written} files across {LAYERS}")
    else:
        print(f"DRY RUN — {len(stems)} tunes, "
              f"{len(hard_report)} with HARD mismatches (best-effort labels):")
        for line in hard_report:
            print(f"  {line}")
        print("re-run with --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
