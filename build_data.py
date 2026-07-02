#!/usr/bin/env python3
"""
Bundle the verified tune JSON files into data/tunes_data.js.

Usage
-----
    python grilles_displayer/build_data.py
    python grilles_displayer/build_data.py --tunes-dir ./tunes_verified
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

IGNORED_STEMS = frozenset({"run_report", "run_state", "verification_state"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tunes-dir",
        default=None,
        help="Directory with verified tune JSON files (default: ../tunes_verified relative to this script)",
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    tunes_dir = Path(args.tunes_dir).resolve() if args.tunes_dir else here.parent / "tunes_verified"
    if not tunes_dir.is_dir():
        print(f"ERROR: tunes directory not found: {tunes_dir}", file=sys.stderr)
        return 1

    tunes = []
    for path in sorted(tunes_dir.glob("*.json")):
        if path.stem in IGNORED_STEMS or path.stem.endswith("_opus"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: failed to read {path.name}: {exc}", file=sys.stderr)
            return 1
        tunes.append({"id": path.stem, **data})

    tunes.sort(key=lambda t: str(t.get("title") or t["id"]).upper())

    out_path = here / "data" / "tunes_data.js"
    out_path.parent.mkdir(exist_ok=True)
    payload = json.dumps(tunes, ensure_ascii=False, indent=1)
    out_path.write_text(f"window.TUNES = {payload};\n", encoding="utf-8")
    print(f"Wrote {len(tunes)} tunes to {out_path.relative_to(here)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
