"""Assemble the final ABC file from skeleton headers + the model's body, and
write it to data/melody/03_wip, with debug artifacts alongside (plan §1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .skeleton import Skeleton


@dataclass
class ReadResult:
    printed_key: str
    abc_body: str
    flags: list[dict]  # [{"bar": int, "reason": str}]
    cost: float = 0.0


def _headers_with_key(skeleton: Skeleton, printed_key: str) -> list[str]:
    """Skeleton headers, but with K: replaced by the model's printed signature."""
    lines = []
    for line in skeleton.header_lines:
        if line.startswith("K:") and printed_key:
            lines.append(f"K:{printed_key}")
        else:
            lines.append(line)
    return lines


def assemble_abc(skeleton: Skeleton, result: ReadResult) -> str:
    """Full ABC text: headers + body + `% flag:` lines for the scorer/reviewer."""
    headers = _headers_with_key(skeleton, result.printed_key)
    body = result.abc_body.strip("\n")
    parts = ["\n".join(headers), body]
    if result.flags:
        flag_lines = [
            f"% flag: {f['bar']} {f.get('reason', '').strip()}".rstrip()
            for f in sorted(result.flags, key=lambda f: f.get("bar", 0))
        ]
        parts.append("\n".join(flag_lines))
    return "\n".join(parts) + "\n"


def build_scaffold(skeleton: Skeleton) -> str:
    """A structure-only ABC template: correct headers + section labels + the
    right number of empty bars (full-measure invisible rests), in house style.

    The VLM read does not clear the accuracy bar (Phase-3 benchmark), so the
    pipeline's product is the STRUCTURE: a reviewer opens this next to the
    manuscript crop and fills in the notes. Every bar is a full-measure
    invisible rest `x<meter>` so the file validates and renders as blank,
    barlined, labeled staves — a fill-in template. One source line = 4 bars,
    `||` at section ends, `|]` at the very end.
    """
    meter_units = int(skeleton.meter_units)
    empty = f"x{meter_units}"
    lines = list(skeleton.header_lines)
    lines.append(
        f"% MELODY SCAFFOLD — structure only (headers + sections + empty bars). "
        f"Fill in notes from {skeleton.unit.melody_file}; the bars are "
        f"placeholder whole-measure rests.")
    if skeleton.needs_printed_key:
        lines.append(f"% CONFIRM KEY: analyzed {skeleton.key_tonic} "
                     f"{skeleton.key_mode}; set K: to the PRINTED signature.")
    for note in skeleton.notes:
        lines.append(f"% note: {note}")
    n_sections = len(skeleton.sections)
    for si, sec in enumerate(skeleton.sections):
        terminal = "|]" if si == n_sections - 1 else "||"
        bars = [empty] * sec.bars
        head = f'"^{sec.label}" '
        for row_start in range(0, len(bars), 4):
            row = bars[row_start:row_start + 4]
            is_last_row = row_start + 4 >= len(bars)
            sep = " | ".join(row)
            end = f" {terminal}" if is_last_row else " |"
            lines.append(f"{head}{sep}{end}" if row_start == 0 else f"{sep}{end}")
            head = ""
    return "\n".join(lines) + "\n"


def wip_path(cfg: Config, stem: str) -> Path:
    return cfg.wip_dir / f"{stem}.abc"


def write_tune(cfg: Config, stem: str, abc_text: str) -> Path:
    cfg.wip_dir.mkdir(parents=True, exist_ok=True)
    path = wip_path(cfg, stem)
    path.write_text(abc_text, encoding="utf-8")
    return path


def write_debug(cfg: Config, stem: str, name: str, payload: dict | str) -> Path:
    d = cfg.debug_dir / stem
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return path
