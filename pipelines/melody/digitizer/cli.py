"""Command-line entry point for the deterministic core (Phase 1, no API).

    python -m pipelines.melody.digitizer.cli discover
    python -m pipelines.melody.digitizer.cli skeleton <stem>
    python -m pipelines.melody.digitizer.cli validate <file.abc> [--stem <stem>]
    python -m pipelines.melody.digitizer.cli strips <stem>
    python -m pipelines.melody.digitizer.cli render <file.abc> <stem>
    python -m pipelines.melody.digitizer.cli score [--wip DIR] [--verified DIR]
    python -m pipelines.melody.digitizer.cli check      # Phase-1 acceptance

`check` proves the deterministic core against the 14 owner-verified tunes:
every skeleton's headers and section plan must match its verified file, and
the validator must pass each verified file clean. Reads/repair passes
(prompt.py/vlm.py/merge.py/runner.py) arrive in Phase 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .manifest import load_units, unit_for_stem
from .render import render_tune
from .score import score_dirs
from .skeleton import build_skeleton
from .strips import render_overlays
from .validation import parse_tune, section_bar_counts, validate_tune


def _skeleton_text(stem: str, cfg: Config, printed_key: str | None = None) -> str:
    units, _ = load_units(cfg)
    unit = unit_for_stem(units, stem)
    sk = build_skeleton(unit, cfg, printed_key=printed_key)
    lines = list(sk.header_lines)
    lines.append("% --- section plan (bars) ---")
    for s in sk.sections:
        lines.append(f"% {s.label}: {s.bars} bars over {s.strain}")
    for note in sk.notes:
        lines.append(f"% note: {note}")
    return "\n".join(lines)


def cmd_discover(cfg: Config) -> int:
    units, stats = load_units(cfg)
    print(f"index rows (both):     {stats.index_rows_both}")
    print(f"processable units:     {stats.units}")
    print(f"  missing melody crop: {len(stats.missing_crop)}")
    print(f"  missing chords JSON: {len(stats.missing_chords)}")
    for u in units[:20]:
        print(f"  {u.stem:45s} <- {u.chords_file}")
    if len(units) > 20:
        print(f"  ... and {len(units) - 20} more")
    return 0


def cmd_skeleton(cfg: Config, stem: str, printed_key: str | None) -> int:
    print(_skeleton_text(stem, cfg, printed_key))
    return 0


def cmd_validate(cfg: Config, path: Path, stem: str | None) -> int:
    text = path.read_text(encoding="utf-8")
    plan = None
    if stem:
        units, _ = load_units(cfg)
        ref = parse_tune(text)
        plan = build_skeleton(unit_for_stem(units, stem), cfg,
                              printed_key=ref.key).plan
    tune, rep = validate_tune(text, plan=plan)
    if tune is not None:
        counts = " ".join(f"{l}:{n}" for l, n in section_bar_counts(tune))
        print(f"sections: {counts}")
    for f in rep.errors:
        print("ERROR", f)
    for f in rep.warnings:
        print("warn ", f)
    print("OK" if rep.ok else "FAILED")
    return 0 if rep.ok else 1


def cmd_strips(cfg: Config, stem: str) -> int:
    units, _ = load_units(cfg)
    unit = unit_for_stem(units, stem)
    out = cfg.debug_dir / stem
    paths = render_overlays(unit.crop_path(cfg), out)
    print(f"wrote {len(paths)} overlay files to {out}")
    return 0


def cmd_render(cfg: Config, path: Path, stem: str) -> int:
    abc = path.read_text(encoding="utf-8")
    html, png, ok, reason = render_tune(abc, stem, cfg)
    print(f"html: {html}\npng:  {png}\ncheck: {'OK' if ok else 'FAIL'} — {reason}")
    return 0 if ok else 1


def cmd_score(cfg: Config, wip: Path, verified: Path) -> int:
    from .examples import EXAMPLE_STEM
    agg = score_dirs(wip, verified, exclude={EXAMPLE_STEM})
    if not agg.tunes:
        print("no scored tunes (no wip .abc with a verified counterpart)")
        return 1
    for t in agg.tunes:
        print(f"{t.stem:45s} exact {t.exact_bars:2d}/{t.compared_bars:2d} "
              f"({t.exact_rate:4.0%}) pitch {t.pitch_acc:5.1%} "
              f"unflagged-wrong {len(t.unflagged_wrong)}")
    print(f"\nAGGREGATE  exact {agg.exact_rate:.1%}  pitch {agg.pitch_acc:.1%}  "
          f"rhythm {agg.rhythm_acc:.1%}  mean unflagged-wrong/tune "
          f"{agg.mean_unflagged_wrong:.2f}")
    print("taxonomy:", dict(agg.taxonomy.most_common()))
    return 0


def cmd_read(cfg: Config, stems: list[str], render: bool) -> int:  # noqa: C901
    """Phase-2 single-read E2E for one or more tunes (spends API budget)."""
    from .runner import read_one
    from .vlm import VLMClient

    units, _ = load_units(cfg)
    client = VLMClient(cfg)
    total_cost = 0.0
    worst = 0
    for stem in stems:
        try:
            unit = unit_for_stem(units, stem)
        except KeyError as exc:
            print(f"{stem}: {exc}")
            worst = max(worst, 2)
            continue
        out = read_one(cfg, client, unit)
        total_cost += out.cost
        n_flags = len(out.flags)
        print(f"\n=== {stem}  [{out.status}]  {out.attempts} call(s)  "
              f"${out.cost:.3f}  key={out.printed_key}  flags={n_flags}")
        if out.report is not None:
            for f in out.report.errors:
                print("  ERROR", f)
            for f in out.report.warnings:
                print("  warn ", f)
        if out.error:
            print("  reason:", out.error[:300])
        if out.status == "error":
            worst = max(worst, 1)
        if render and out.abc_text:
            from .render import render_tune
            _, png, ok, reason = render_tune(out.abc_text, stem, cfg)
            print(f"  render: {'OK' if ok else 'FAIL'} — {reason}  ({png})")
    print(f"\nTOTAL COST: ${total_cost:.3f} over {len(stems)} tune(s) "
          f"(avg ${total_cost / max(1, len(stems)):.3f}/tune)")
    return worst


def cmd_check(cfg: Config) -> int:
    """Phase-1 acceptance: skeleton headers+plan and validation vs 14 verified."""
    units, _ = load_units(cfg)
    n_h = n_p = n_v = total = 0
    failures: list[str] = []
    for abc in sorted(cfg.verified_dir.glob("*.abc")):
        total += 1
        text = abc.read_text(encoding="utf-8")
        try:
            unit = unit_for_stem(units, abc.stem)
        except KeyError:
            failures.append(f"{abc.stem}: no processable unit (chords JSON missing?)")
            continue
        ref = parse_tune(text)
        sk = build_skeleton(unit, cfg, printed_key=ref.key)
        h_ok = sk.header_lines == ref.header_lines
        p_ok = sk.plan == section_bar_counts(ref)
        tune, rep = validate_tune(text, plan=sk.plan)
        v_ok = rep.ok and not any(w.code == "label" for w in rep.warnings)
        n_h += h_ok
        n_p += p_ok
        n_v += v_ok
        ov = " (override)" if unit.override_path(cfg).is_file() else ""
        mark = "OK " if (h_ok and p_ok and v_ok) else "FAIL"
        print(f"{mark} {abc.stem:45s} headers={'MATCH' if h_ok else 'DIFF'} "
              f"plan={'MATCH' if p_ok else 'DIFF'} "
              f"validate={'OK' if v_ok else 'FAIL'}{ov}")
        if not h_ok:
            for a, b in zip(sk.header_lines, ref.header_lines):
                if a != b:
                    failures.append(f"{abc.stem}: header\n    skel {a!r}\n    ref  {b!r}")
        if not p_ok:
            failures.append(f"{abc.stem}: plan {sk.plan} vs {section_bar_counts(ref)}")
        if not v_ok:
            for f in rep.errors:
                failures.append(f"{abc.stem}: {f}")
    print(f"\nheaders {n_h}/{total}  plans {n_p}/{total}  validate {n_v}/{total}")
    for f in failures:
        print("  -", f)
    ok = n_h == n_p == n_v == total
    print("PHASE-1 ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="melody-digitizer", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    sp = sub.add_parser("skeleton"); sp.add_argument("stem"); sp.add_argument("--printed-key", default=None)
    sp = sub.add_parser("validate"); sp.add_argument("path", type=Path); sp.add_argument("--stem", default=None)
    sp = sub.add_parser("strips"); sp.add_argument("stem")
    sp = sub.add_parser("render"); sp.add_argument("path", type=Path); sp.add_argument("stem")
    sp = sub.add_parser("score")
    sp.add_argument("--wip", type=Path, default=None)
    sp.add_argument("--verified", type=Path, default=None)
    sp = sub.add_parser("read")
    sp.add_argument("stems", nargs="+", help="melody stem(s) to read")
    sp.add_argument("--render", action="store_true", help="also render a lead sheet")
    sp.add_argument("--retries", type=int, default=None,
                    help="hard-failure retries per tune (default from Config)")
    sub.add_parser("check")
    args = p.parse_args(argv)

    cfg = Config()
    if args.cmd == "discover":
        return cmd_discover(cfg)
    if args.cmd == "skeleton":
        return cmd_skeleton(cfg, args.stem, args.printed_key)
    if args.cmd == "validate":
        return cmd_validate(cfg, args.path, args.stem)
    if args.cmd == "strips":
        return cmd_strips(cfg, args.stem)
    if args.cmd == "render":
        return cmd_render(cfg, args.path, args.stem)
    if args.cmd == "score":
        return cmd_score(cfg, args.wip or cfg.wip_dir, args.verified or cfg.verified_dir)
    if args.cmd == "read":
        if args.retries is not None:
            import dataclasses
            cfg = dataclasses.replace(cfg, retries=max(1, args.retries))
        return cmd_read(cfg, args.stems, args.render)
    if args.cmd == "check":
        return cmd_check(cfg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
