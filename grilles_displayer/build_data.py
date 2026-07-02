#!/usr/bin/env python3
"""
Bundle the verified tune JSON files into data/tunes_data.js.

Also copies each tune's cropped scan (crops/<id>.png) into
grilles_displayer/crops/ so the deployed app (GitHub Pages uploads only
grilles_displayer/) can show the original image next to the grid.

Usage
-----
    python grilles_displayer/build_data.py
    python grilles_displayer/build_data.py --tunes-dir ./tunes_verified --crops-dir ./crops

    manual deployment:
    git checkout main
    git pull
    git subtree push --prefix=grilles_displayer origin gh-pages
    git checkout main
"""
from __future__ import annotations

import argparse
import json
import shutil
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
    parser.add_argument(
        "--crops-dir",
        default=None,
        help="Directory with cropped tune scans, <id>.png (default: ../crops relative to this script)",
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    tunes_dir = Path(args.tunes_dir).resolve() if args.tunes_dir else here.parent / "tunes_verified"
    if not tunes_dir.is_dir():
        print(f"ERROR: tunes directory not found: {tunes_dir}", file=sys.stderr)
        return 1
    crops_dir = Path(args.crops_dir).resolve() if args.crops_dir else here.parent / "crops"
    out_crops = here / "crops"

    tunes = []
    for path in sorted(tunes_dir.glob("*.json")):
        if path.stem in IGNORED_STEMS or path.stem.endswith("_opus"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: failed to read {path.name}: {exc}", file=sys.stderr)
            return 1
        record = {"id": path.stem, **data}
        png = crops_dir / f"{path.stem}.png"
        if png.is_file():
            out_crops.mkdir(exist_ok=True)
            shutil.copyfile(png, out_crops / png.name)
            record["image"] = f"crops/{png.name}"
        tunes.append(record)

    # Drop stale copies of tunes no longer bundled.
    if out_crops.is_dir():
        keep = {f"{t['id']}.png" for t in tunes if "image" in t}
        for stale in out_crops.glob("*.png"):
            if stale.name not in keep:
                stale.unlink()

    tunes.sort(key=lambda t: str(t.get("title") or t["id"]).upper())

    out_path = here / "data" / "tunes_data.js"
    out_path.parent.mkdir(exist_ok=True)
    payload = json.dumps(tunes, ensure_ascii=False, indent=1)
    out_path.write_text(f"window.TUNES = {payload};\n", encoding="utf-8")
    with_image = sum(1 for t in tunes if "image" in t)
    print(f"Wrote {len(tunes)} tunes to {out_path.relative_to(here)} ({with_image} with images in crops/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
