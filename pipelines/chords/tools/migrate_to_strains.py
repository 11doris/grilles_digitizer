#!/usr/bin/env python3
"""Phase C migration: legacy `sections` map -> explicit `strains` list
(docs/specs/strain_model_phase_c_plan.md §6).

Usage
-----
    python pipelines/chords/tools/migrate_to_strains.py --check
        # equivalence gate only: migrate every tune IN MEMORY and assert
        # byte-identical slots, labels, bar totals and anchor resolution
        # against the legacy reader; writes nothing
    python pipelines/chords/tools/migrate_to_strains.py --write
        # migrate data/chords/{03_wip,04_verified,05_annotated} in place
        # (runs the gate per file first; a failing file is left untouched)
    # optional explicit dirs after the flag

Also importable: `tune_to_strains` is the single reshape used both here and
by the verifier's ingest conversion (02_raw stays a legacy section map
forever), and `strains_to_sections` is the inverse used to keep the
digitizer few-shot examples in the model's raw output shape.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from pipelines.chords.similarity.normalize import (  # noqa: E402
    AUX_CONNECTORS, _key_fallback_label, _strain_of_key, derive_labels,
    derived_form_strains, expand_tune, form_printed, iter_parts, part_ids,
    resolve_anchor, sections_view, validate_strains,
)

IGNORED_STEMS = {"verification_state", "run_report", "run_state"}


# ---------------------------------------------------------------------------
# Legacy map -> strains reshape
# ---------------------------------------------------------------------------

def _legacy_groups(sections: dict) -> list[tuple[str | None, list[str]]]:
    """Consecutive runs of section keys sharing a strain, in document order.
    Aux keys (strain None) each form their own single-key run."""
    runs: list[tuple[str | None, list[str]]] = []
    for key in sections:
        strain = _strain_of_key(key)
        if strain is not None and runs and runs[-1][0] == strain:
            runs[-1][1].append(key)
        else:
            runs.append((strain, [key]))
    return runs


def _strain_meta(strain_key: str | None, first_key: str) -> tuple[str, str]:
    """(name, role) for a legacy strain group (plan §4 migration mapping)."""
    if strain_key == "chorus":
        return "chorus", "chorus"
    if strain_key == "verse":
        return "verse", "verse"
    if strain_key is None:  # bare / capitalised connector key
        return first_key.lower(), "aux"
    return strain_key, "strain"


def tune_to_strains(tune: dict) -> dict:
    """Reshape one legacy tune to the strains model. Idempotent: a tune that
    already carries `strains` is returned unchanged.

    Reads the already-derived, already-verified `section_labels` +
    `form_strains` (falling back to a fresh `derive_labels` alignment when
    absent, e.g. raw digitizer output), so the mapping matches what was
    verified. Rewrites every anchor and per-part map from the old section
    keys to the new addressing; drops `form_strains` and `section_labels`;
    keeps `form` verbatim.
    """
    if "strains" in tune:
        return tune
    sections = tune.get("sections") or {}
    structured, derived, _warnings = derive_labels(tune)
    labels = dict(derived)
    labels.update(tune.get("section_labels") or {})
    form_strains = tune.get("form_strains") or {
        k: v for k, v in structured.items() if k != "printed"}

    strains: list[dict] = []
    key_map: dict[str, tuple[str, int]] = {}  # old key -> (strain name, part)
    for strain_key, keys in _legacy_groups(sections):
        name, role = _strain_meta(strain_key, keys[0])
        parts: list[dict] = []
        for key in keys:
            part = {"label": labels.get(key) or _key_fallback_label(key)}
            seq = (form_strains.get(strain_key or keys[0]) or {}).get("labels")
            if (len(keys) == 1 and seq and len(seq) > 1
                    and len(set(seq)) == 1 and seq[0] == part["label"]):
                # the "identical parts stored once" repeat, now explicit
                part["plays"] = len(seq)
            part["bars"] = sections[key]
            key_map[key] = (name, len(parts))
            parts.append(part)
        strains.append({"name": name, "role": role, "parts": parts})

    out: dict = {}
    for field, value in tune.items():
        if field in ("form_strains", "section_labels"):
            continue  # derived under Phase C, never stored (plan §5)
        if field == "sections":
            out["strains"] = strains
        else:
            out[field] = value

    def anchor(old: dict) -> dict:
        name, part = key_map[old["section"]]
        new = {"strain": name, "part": part}
        if old.get("bar") is not None:
            new["bar"] = old["bar"]
        return new

    if out.get("variants"):
        out["variants"] = [
            {**v, "targets": [anchor(t) for t in v["targets"]]}
            if v.get("targets") else dict(v)
            for v in out["variants"]]
    if out.get("coda_jump", {}).get("from"):
        cj = dict(out["coda_jump"])
        cj["from"] = anchor(cj["from"])
        out["coda_jump"] = cj

    # Per-part maps: old section key -> generated part id. section_keys must
    # map exactly; fingerprint prose keys are remapped best-effort (they
    # already drift on a few tunes and are display-only).
    new_ids: dict[str, str] = {}
    for strain in strains:
        ids = part_ids(strain)
        for old, (name, idx) in key_map.items():
            if name == strain["name"]:
                new_ids[old] = ids[idx]

    def remap(mapping: dict, strict: bool) -> dict:
        remapped = {}
        for old, value in mapping.items():
            if old in new_ids:
                remapped[new_ids[old]] = value
            elif strict:
                raise ValueError(f"section_keys key {old!r} matches no section")
            else:
                remapped[old] = value
        return remapped

    if out.get("section_keys"):
        out["section_keys"] = remap(out["section_keys"], strict=True)
    fp = out.get("harmonic_fingerprint")
    if fp and isinstance(fp.get("sections"), dict):
        fp = dict(fp)
        fp["sections"] = remap(fp["sections"], strict=False)
        out["harmonic_fingerprint"] = fp
    ka = out.get("key_annotation")
    if ka:
        ka = dict(ka)
        for field in ("scorer", "llm"):
            vote = ka.get(field)
            if isinstance(vote, dict) and isinstance(
                    vote.get("section_keys"), dict):
                vote = dict(vote)
                vote["section_keys"] = remap(vote["section_keys"],
                                             strict=False)
                ka[field] = vote
        if isinstance(ka.get("section_key_proposals"), dict):
            ka["section_key_proposals"] = remap(
                ka["section_key_proposals"], strict=False)
        out["key_annotation"] = ka
    return out


# ---------------------------------------------------------------------------
# Strains -> legacy map (digitizer few-shot examples only)
# ---------------------------------------------------------------------------

def strains_to_sections(tune: dict) -> dict:
    """Down-convert a strains-model tune to the digitizer's raw output shape:
    a `sections` map keyed by the generated part ids, anchors back to
    {section, bar}. Used by build_examples.py so the few-shot examples keep
    teaching the model the raw shape (02_raw never changes shape)."""
    if "strains" not in tune:
        return tune
    ids: dict[tuple[str, int], str] = {}
    sections: dict[str, list] = {}
    for strain in tune["strains"]:
        for i, (pid, part) in enumerate(zip(part_ids(strain),
                                            strain.get("parts") or [])):
            ids[(strain["name"], i)] = pid
            sections[pid] = part.get("bars") or []

    def anchor(new: dict) -> dict:
        old = {"section": ids[(new["strain"], new["part"])]}
        if new.get("bar") is not None:
            old["bar"] = new["bar"]
        return old

    out: dict = {}
    for field, value in tune.items():
        if field == "strains":
            out["sections"] = sections
        else:
            out[field] = value
    if out.get("variants"):
        out["variants"] = [
            {**v, "targets": [anchor(t) for t in v["targets"]]}
            if v.get("targets") else dict(v)
            for v in out["variants"]]
    if out.get("coda_jump", {}).get("from"):
        cj = dict(out["coda_jump"])
        cj["from"] = anchor(cj["from"])
        out["coda_jump"] = cj
    return out


# ---------------------------------------------------------------------------
# Equivalence gate (plan §6): the migration is not accepted until clean
# ---------------------------------------------------------------------------

def _slot_signature(tune: dict) -> list[tuple]:
    """Every expanded unit as (chords...) tuples, in document order — the
    section/part names are deliberately NOT part of the signature (ids may
    legitimately differ from historical keys), the music must not change."""
    return [tuple((s.chord.symbol, s.bar, s.half) for s in slots)
            for slots in expand_tune(tune).values()]


def _old_global_bar(tune: dict, section: str, bar: int) -> int:
    off = 0
    for name, bars in (tune.get("sections") or {}).items():
        if name == section:
            return off + bar
        off += len(bars or [])
    raise ValueError(f"anchor section {section!r} not found")


def _new_global_bar(tune: dict, anchor: dict) -> int:
    strain, part, _pid = resolve_anchor(tune, anchor)
    off = 0
    for _pid2, s, p in iter_parts(tune):
        if p is part:
            return off + anchor["bar"]
        off += len(p.get("bars") or [])
    raise ValueError("anchor part not found in iteration")


def check_equivalence(old: dict, new: dict) -> list[str]:
    """All gate assertions for one tune; returns problems (empty = clean)."""
    problems: list[str] = []
    if validate_strains(new):
        problems += [f"validate: {e}" for e in validate_strains(new)]

    if _slot_signature(old) != _slot_signature(new):
        problems.append("expanded slots differ")

    # derived labels + bar totals must match the stored form_strains. The
    # stored bars number comes from the PRINTED form (or prose), which on a
    # couple of charts disagrees with the stored music (e.g. "42 A A' B A"
    # over 44 stored bars); the derived number is the stored reality, so a
    # bars mismatch only fails the gate when it also disagrees with the
    # actual stored bar count of the legacy group.
    stored = old.get("form_strains") or {}
    derived = derived_form_strains(new)
    old_group_bars: dict[str, int] = {}
    for key, bars in (old.get("sections") or {}).items():
        gid = _strain_of_key(key) or key.lower()
        old_group_bars[gid] = old_group_bars.get(gid, 0) + len(bars or [])
    for name, entry in stored.items():
        got = derived.get(name)
        if got is None:
            problems.append(f"form_strains[{name!r}] lost")
            continue
        if entry.get("labels") != got["labels"]:
            problems.append(f"form_strains[{name!r}].labels "
                            f"{entry.get('labels')} -> {got['labels']}")
        if entry.get("bars") not in (None, got["bars"]) \
                and got["bars"] != old_group_bars.get(name):
            problems.append(f"form_strains[{name!r}].bars "
                            f"{entry.get('bars')} -> {got['bars']}")

    # anchors must resolve to the same global bar
    old_anchors = []
    for v in old.get("variants") or []:
        old_anchors += list(v.get("targets") or [])
    if (old.get("coda_jump") or {}).get("from"):
        old_anchors.append(old["coda_jump"]["from"])
    new_anchors = []
    for v in new.get("variants") or []:
        new_anchors += list(v.get("targets") or [])
    if (new.get("coda_jump") or {}).get("from"):
        new_anchors.append(new["coda_jump"]["from"])
    for old_a, new_a in zip(old_anchors, new_anchors):
        try:
            ob = _old_global_bar(old, old_a["section"], old_a.get("bar") or 1)
            nb = _new_global_bar(new, {**new_a,
                                       "bar": new_a.get("bar") or 1})
            if ob != nb:
                problems.append(f"anchor {old_a} -> {new_a}: "
                                f"global bar {ob} != {nb}")
        except ValueError as exc:
            problems.append(f"anchor {old_a}: {exc}")

    # per-part maps: same count, values untouched
    old_sk = old.get("section_keys") or {}
    new_sk = new.get("section_keys") or {}
    if len(old_sk) != len(new_sk) or \
            sorted(map(str, old_sk.values())) != sorted(map(str,
                                                            new_sk.values())):
        problems.append(f"section_keys changed: {old_sk} -> {new_sk}")

    # idempotence
    if tune_to_strains(new) is not new:
        problems.append("migration is not idempotent")

    # round trip: the down-conversion reproduces the music
    back = strains_to_sections(new)
    if [list(b) for b in _slot_signature(back)] != \
            [list(b) for b in _slot_signature(old)]:
        problems.append("strains_to_sections round-trip differs")
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _tune_paths(dirname: Path) -> list[Path]:
    return [p for p in sorted(dirname.glob("*.json"))
            if p.stem not in IGNORED_STEMS and not p.stem.endswith("_opus")
            and not p.name.endswith(".error.json")]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dirs", nargs="*", default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="equivalence gate only, write nothing")
    mode.add_argument("--write", action="store_true",
                      help="migrate in place (gate per file first)")
    args = parser.parse_args()

    dirs = [Path(d) for d in args.dirs] if args.dirs else [
        repo / "data" / "chords" / "03_wip",
        repo / "data" / "chords" / "04_verified",
        repo / "data" / "chords" / "05_annotated",
    ]

    total = failed = migrated = skipped = 0
    renames: list[str] = []
    for dirname in dirs:
        print(f"===== {dirname} =====")
        for path in _tune_paths(dirname):
            old = json.loads(path.read_text("utf-8"))
            if "strains" in old:
                skipped += 1
                continue
            if "sections" not in old:
                print(f"  {path.stem}: no sections — skipped")
                skipped += 1
                continue
            total += 1
            new = tune_to_strains(old)
            problems = check_equivalence(old, new)
            if problems:
                failed += 1
                print(f"  GATE FAILED {path.name}")
                for p in problems:
                    print(f"      {p}")
                continue
            for old_key, new_id in zip(sections_view(old),
                                       sections_view(new)):
                if old_key != new_id:
                    renames.append(f"{path.stem}: {old_key} -> {new_id}")
            if args.write:
                indent = 1 if dirname.name == "05_annotated" else 2
                trailing = "\n" if dirname.name == "05_annotated" else ""
                path.write_text(
                    json.dumps(new, indent=indent, ensure_ascii=False)
                    + trailing, "utf-8")
                migrated += 1
    if renames:
        print(f"\n{len(renames)} part id(s) differ from the historical "
              "section keys (label-derived):")
        for r in renames:
            print(f"  {r}")
    print(f"\n{total} legacy tunes checked, {failed} gate failures, "
          f"{migrated} migrated, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
