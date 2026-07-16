"""Mine the annotated corpus for new harmonic building blocks.

Slides windows of N consecutive printed chords (default N = 3..6) through
every part of every tune in data/chords/05_annotated, under the analyzer's
own key contexts, and counts each window's degree:quality sequence exactly
the way the catalog matcher would match it (consecutive sounding events,
one key context, bounded bar span, no reprints inside the window).
Sequences that

* recur in at least --min-tunes tunes,
* are not in catalog.json yet,
* are not already mostly covered by the blocks the current catalog finds
  (at most --max-covered of their occurrences inside existing block spans),
* and look like a block rather than a phrase seam: windows that start on a
  V7–I resolution into the next phrase, pad an existing catalog block by
  one chord, or sit inside one's definition are dropped

are candidate blocks. The report prints them with counts, example tunes
and a ready-made catalog entry; --write appends those entries to
catalog.json. Appended blocks are `generic: true` — generic blocks only
label chords no named block, ii–V chain, dominant cycle or root-motion
run claims, so an automatic append can never take over a better name.
Naming a block (a Cadence/Turnaround/Opening/Ending/Bridge name) or
promoting it to non-generic stays a human edit of the appended entry.

Generated names follow the catalog rule: a bare degree formula with ♭/♯
glyphs, 7 = dominant, o = diminished, ø = half-diminished, lowercase =
minor, en-dashes; the id mirrors the formula in ASCII (b/s accidental
prefix; 7/o/h suffixes; a minor degree gets an m suffix only when the
same degree also appears with another quality, as in iv_ivm_i). After a
--write, regenerate the derived data:

    python -m pipelines.chords.annotate_keys --reuse-annotation
    python apps/displayer/build_data.py

Usage
-----
    python -m pipelines.chords.harmonic_analysis.mine_blocks           # report
    python -m pipelines.chords.harmonic_analysis.mine_blocks --write   # extend
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipelines.chords.harmonic_analysis.analyze import (
    _CATALOG_PATH, _UPPER_DEGREE, _ctx_of, _detect_regions,
    _local_keys_by_part, _part_events, analyze_annotated, load_catalog,
)
from pipelines.chords.similarity.normalize import ChordParseError, sections_view

ANNOTATED_DIR = (Path(__file__).resolve().parents[3]
                 / "data" / "chords" / "05_annotated")

# quality class -> (degree case, formula suffix, ascii id suffix)
_QUALITY_FORM = {
    "maj":  ("upper", "", ""),
    "any":  ("upper", "", ""),
    "dom":  ("upper", "7", "7"),
    "min":  ("lower", "", ""),   # id gets an "m" only on a degree clash
    "m7b5": ("lower", "ø", "h"),
    "dim":  ("upper", "o", "o"),
    "sus":  ("upper", "sus", "sus"),
    "aug":  ("upper", "+", "aug"),
}
_GLYPH = {"b": "♭", "#": "♯", "": ""}


def _token_forms(pc: int, quality: str) -> tuple[str, str, str]:
    """(formula token, ascii id token, pattern token) for one degree."""
    deg = _UPPER_DEGREE[pc]
    acc, letters = (deg[0], deg[1:]) if deg[0] in "b#" else ("", deg)
    case, suffix, id_suffix = _QUALITY_FORM[quality]
    body = letters.lower() if case == "lower" else letters
    formula = _GLYPH[acc] + body + suffix
    ascii_id = ("s" if acc == "#" else acc) + letters.lower() + id_suffix
    pat_deg = acc + (letters.lower() if quality in ("min", "m7b5") else letters)
    return formula, ascii_id, f"{pat_deg}:{quality}"


def entry_for(tokens: tuple[tuple[int, str], ...]) -> dict:
    """A ready-made generic catalog entry for a mined degree sequence."""
    forms = [_token_forms(pc, q) for pc, q in tokens]
    ids = [f[1] for f in forms]
    for i, (pc, q) in enumerate(tokens):
        # A minor degree needs the "m" disambiguator only against a bare
        # ascii twin (IV vs iv -> iv/ivm; II7 vs ii are already distinct).
        if q == "min" and any(pc2 == pc and q2 in ("maj", "any")
                              for pc2, q2 in tokens):
            ids[i] += "m"
    return {
        "id": "_".join(ids),
        "name": "–".join(f[0] for f in forms),
        "pattern": " ".join(f[2] for f in forms),
        "max_bars": min(2 * len(tokens), 8),
        "generic": True,
    }


def _seq_matches(tokens, entry_tokens) -> bool:
    """Whether an entry's token list matches this exact degree sequence
    (an entry's "any" quality is a wildcard)."""
    return len(entry_tokens) == len(tokens) and all(
        pc == epc and eq in ("any", q)
        for (pc, q), (epc, eq) in zip(tokens, entry_tokens))


def in_catalog(tokens, catalog: list[dict]) -> bool:
    return any(_seq_matches(tokens, v)
               for e in catalog for v in e["_variants"])


def is_seam(tokens, catalog: list[dict]) -> bool:
    """Windows that straddle boundaries instead of being a block: opening
    on a V7–I resolution (the tail of the previous phrase's cadence), an
    existing catalog block padded by one leading or trailing chord, or a
    stretch lying inside an existing block's definition."""
    if tokens[0] == (7, "dom") and tokens[1][0] == 0:
        return True
    for entry in catalog:
        for etoks in entry["_variants"]:
            if len(etoks) == len(tokens) - 1 and (
                    _seq_matches(tokens[1:], etoks)
                    or _seq_matches(tokens[:-1], etoks)):
                return True
            if len(etoks) > len(tokens) and any(
                    _seq_matches(tokens, etoks[i:i + len(tokens)])
                    for i in range(len(etoks) - len(tokens) + 1)):
                return True
    return False


def _iter_parts(tune: dict):
    """(part id, events, ctx_at) for each analyzable part, with the same
    key contexts the block matcher sees."""
    local = _local_keys_by_part(tune, tune.get("section_keys"))
    for pid, bars in sections_view(tune).items():
        try:
            events = _part_events(bars)
        except ChordParseError:
            continue
        if not events:
            continue
        part_ctx = _ctx_of(local[pid]) if pid in local else _ctx_of(tune["key"])
        regions = _detect_regions(events, part_ctx, pid, [])

        def ctx_at(idx, regions=regions, part_ctx=part_ctx):
            for reg in regions:
                if reg["_first"] <= idx <= reg["_last"]:
                    return reg["_ctx"]
            return part_ctx

        yield pid, events, ctx_at


def mine(docs, ns=(3, 4, 5, 6)) -> dict:
    """{degree sequence: {"tunes", "occ", "cov"}} over (stem, tune) docs.

    An occurrence is a window the matcher could match: consecutive events
    in one key context, span <= the max_bars its entry would get, and no
    same-chord reprint inside (the collapsed form counts elsewhere). "cov"
    counts occurrences already lying inside the current catalog's blocks.
    """
    stats: dict = {}
    for stem, tune in docs:
        analysis = analyze_annotated(tune)
        for pid, events, ctx_at in _iter_parts(tune):
            part = (analysis.get("parts") or {}).get(pid) or {}
            pos = {(e.bar, e.beat): e.idx for e in events}
            covered: set[int] = set()
            for block in part.get("blocks") or []:
                i, j = pos[tuple(block["from"])], pos[tuple(block["to"])]
                covered.update(range(i, j + 1))
            for n in ns:
                max_bars = min(2 * n, 8)
                for s in range(len(events) - n + 1):
                    win = events[s:s + n]
                    if win[-1].bar - win[0].bar + 1 > max_bars:
                        continue
                    ctx = ctx_at(win[0].idx)
                    if any(not ctx_at(e.idx).same(ctx) for e in win[1:]):
                        continue
                    toks = tuple(((e.chord.root_pc - ctx.tonic_pc) % 12,
                                  e.chord.quality) for e in win)
                    if any(a == b for a, b in zip(toks, toks[1:])):
                        continue
                    st = stats.setdefault(
                        toks, {"tunes": set(), "occ": 0, "cov": 0})
                    st["occ"] += 1
                    st["tunes"].add(stem)
                    if all(i in covered
                           for i in range(win[0].idx, win[-1].idx + 1)):
                        st["cov"] += 1
    return stats


def candidates(stats: dict, catalog: list[dict], *, min_tunes: int,
               max_covered: float) -> list[tuple[tuple, dict]]:
    """The mined sequences worth cataloging, most widespread first."""
    out = [(toks, st) for toks, st in stats.items()
           if len(st["tunes"]) >= min_tunes
           and st["cov"] <= max_covered * st["occ"]
           and not in_catalog(toks, catalog)
           and not is_seam(toks, catalog)]
    out.sort(key=lambda c: (-len(c[1]["tunes"]), -c[1]["occ"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotated", default=str(ANNOTATED_DIR),
                        help="annotated tune directory")
    parser.add_argument("--catalog", default=str(_CATALOG_PATH),
                        help="block catalog to check and extend")
    parser.add_argument("--min-tunes", type=int, default=12,
                        help="a block must recur in this many tunes")
    parser.add_argument("--max-covered", type=float, default=0.5,
                        help="skip sequences whose occurrences already lie "
                             "inside existing blocks beyond this share")
    parser.add_argument("--ns", type=int, nargs="+", default=[3, 4, 5, 6],
                        help="window lengths (chords per block)")
    parser.add_argument("--top", type=int, default=25,
                        help="report (and write) at most N candidates")
    parser.add_argument("--write", action="store_true",
                        help="append the reported candidates to the catalog "
                             "as generic blocks")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):  # glyphs on Windows consoles
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    catalog_path = Path(args.catalog)
    catalog = load_catalog(catalog_path)
    docs = []
    for path in sorted(Path(args.annotated).glob("*.json")):
        tune = json.loads(path.read_text("utf-8"))
        if "key" in tune:
            docs.append((path.stem, tune))
    print(f"{len(docs)} annotated tunes, {len(catalog)} catalog blocks")

    stats = mine(docs, ns=tuple(args.ns))
    found = candidates(stats, catalog, min_tunes=args.min_tunes,
                       max_covered=args.max_covered)[:args.top]
    if not found:
        print("no new block candidates at these thresholds")
        return 0

    entries = []
    existing_ids = {e["id"] for e in catalog}
    for toks, st in found:
        entry = entry_for(toks)
        if entry["id"] in existing_ids:
            print(f"  (skipping {entry['name']}: id {entry['id']} taken)")
            continue
        share = st["cov"] / st["occ"]
        examples = ", ".join(sorted(st["tunes"])[:3])
        print(f"  {len(st['tunes']):3d} tunes {st['occ']:4d} occ "
              f"{share:4.0%} covered  {entry['name']:24s} "
              f"[{entry['pattern']}]  e.g. {examples}")
        entries.append(entry)

    if not args.write:
        print("\nproposed catalog entries (rerun with --write to append):")
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0

    current = json.loads(catalog_path.read_text("utf-8"))
    current.extend(entries)
    catalog_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n", "utf-8")
    load_catalog(catalog_path)  # round-trip: the matcher can parse them
    print(f"\n{len(entries)} block(s) appended to {catalog_path}")
    print("now refresh the derived data:\n"
          "  python -m pipelines.chords.annotate_keys --reuse-annotation\n"
          "  python apps/displayer/build_data.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
