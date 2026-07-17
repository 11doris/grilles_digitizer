"""Single-read orchestration for Phase 2 (plan §4, §9.2).

One tune: clean crop → one VLM read pass → assemble ABC (skeleton headers +
model body) → validate → write 03_wip/<stem>.abc + debug. Retries a hard
validation failure once with a stricter reminder. Dual-read + repair (§4) and
the resumable batch runner (§9.4) arrive in Phase 3/4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...chords.digitizer.images import prepare_crop
from .config import Config
from .manifest import MelodyUnit
from .merge import MergeResult, assemble_body, merge_reads
from .output import ReadResult, assemble_abc, write_debug, write_tune
from .prompt import (STRICTER_REMINDER, build_pass_b_content,
                     build_repair_content, build_user_content)
from .skeleton import Skeleton, build_skeleton
from .strips import build_tracks, overlay_strips_b64
from .validation import Report, parse_tune, validate_tune
from .vlm import VLMClient, VLMRefusal, VLMTruncated, usage_cost


@dataclass
class ReadOutcome:
    stem: str
    status: str  # "ok" | "flagged" | "error"
    abc_text: str = ""
    report: Report | None = None
    attempts: int = 0
    cost: float = 0.0
    printed_key: str = ""
    flags: list[dict] = field(default_factory=list)
    error: str = ""


def read_one(cfg: Config, client: VLMClient, unit: MelodyUnit) -> ReadOutcome:
    """One tune, single read pass. Writes 03_wip on any parseable result."""
    skeleton = build_skeleton(unit, cfg)
    image_b64, media_type = prepare_crop(
        unit.crop_path(cfg), dilate=cfg.dilate, max_long_edge=cfg.max_long_edge)
    user_content = build_user_content(skeleton, image_b64, media_type)

    last_error = ""
    total_cost = 0.0
    max_tokens = cfg.max_output_tokens
    for attempt in range(1, cfg.retries + 1):
        reminder = STRICTER_REMINDER * (attempt - 1)
        try:
            data = client.read(user_content, extra_reminder=reminder,
                               max_tokens=max_tokens)
        except VLMTruncated as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            max_tokens = min(max_tokens * 2, 8000)
            continue
        except VLMRefusal as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        total_cost += usage_cost(cfg.model, client.last_usage)

        result = ReadResult(
            printed_key=str(data.get("printed_key", skeleton.printed_key)).strip(),
            abc_body=str(data.get("abc_body", "")),
            flags=list(data.get("uncertain_bars", [])),
            cost=total_cost,
        )
        abc_text = assemble_abc(skeleton, result)
        write_debug(cfg, unit.stem, "read_a.json", data)
        write_tune(cfg, unit.stem, abc_text)
        tune, report = validate_tune(abc_text, plan=skeleton.plan)

        if report.ok:
            return ReadOutcome(
                stem=unit.stem, status="ok", abc_text=abc_text, report=report,
                attempts=attempt, cost=total_cost,
                printed_key=result.printed_key, flags=result.flags)
        # hard failure — retry once more with a stricter reminder if budget left
        last_error = "; ".join(str(e) for e in report.errors)
        if attempt < cfg.retries:
            continue
        # out of retries: keep the best-so-far file, mark flagged
        return ReadOutcome(
            stem=unit.stem, status="flagged", abc_text=abc_text, report=report,
            attempts=attempt, cost=total_cost, printed_key=result.printed_key,
            flags=result.flags, error=last_error)

    return ReadOutcome(stem=unit.stem, status="error", attempts=cfg.retries,
                       cost=total_cost, error=last_error)


# ------------------------------------------------------------ dual read + repair

@dataclass
class DualOutcome:
    stem: str
    status: str  # "ok" | "flagged" | "error"
    abc_text: str = ""
    report: Report | None = None
    calls: int = 0
    cost: float = 0.0
    printed_key: str = ""
    agreement: float = 0.0
    flagged: list[int] = field(default_factory=list)
    error: str = ""


def _one_read(client: VLMClient, cfg: Config, content: list[dict]) -> tuple[dict, float]:
    """One read call with a truncation bump; returns (tool_input, cost)."""
    max_tokens = cfg.max_output_tokens
    for _ in range(2):
        try:
            data = client.read(content, max_tokens=max_tokens)
            return data, usage_cost(cfg.model, client.last_usage)
        except VLMTruncated:
            max_tokens = min(max_tokens * 2, 8000)
    data = client.read(content, max_tokens=max_tokens)
    return data, usage_cost(cfg.model, client.last_usage)


def read_dual(cfg: Config, client: VLMClient, unit: MelodyUnit) -> DualOutcome:
    """Pass A (full crop) + Pass B (ruler strips) → merge → repair flagged bars.
    Enforces the per-tune call cap (cfg.max_calls_per_tune)."""
    skeleton = build_skeleton(unit, cfg)
    debug = {}
    cost = 0.0
    calls = 0

    # Pass A — full page
    image_b64, media_type = prepare_crop(
        unit.crop_path(cfg), dilate=cfg.dilate, max_long_edge=cfg.max_long_edge)
    a_content = build_user_content(skeleton, image_b64, media_type)
    try:
        a_data, c = _one_read(client, cfg, a_content); cost += c; calls += 1
    except (VLMRefusal, VLMTruncated) as exc:
        return DualOutcome(unit.stem, "error", cost=cost, calls=calls,
                           error=f"pass A: {exc}")
    printed_key = str(a_data.get("printed_key", skeleton.printed_key)).strip()
    abc_a = _assemble(skeleton, printed_key, a_data)
    debug["read_a"] = a_data

    # Pass B — ruler strips
    tiles = overlay_strips_b64(unit.crop_path(cfg), cfg.debug_dir / unit.stem)
    b_content = build_pass_b_content(skeleton, tiles)
    try:
        b_data, c = _one_read(client, cfg, b_content); cost += c; calls += 1
    except (VLMRefusal, VLMTruncated) as exc:
        b_data = {"abc_body": "", "printed_key": printed_key, "uncertain_bars": []}
        debug["pass_b_error"] = str(exc)
    abc_b = _assemble(skeleton, printed_key, b_data)
    debug["read_b"] = b_data

    # Merge
    merge = merge_reads(skeleton, abc_a, abc_b)
    self_flags = {int(f.get("bar", -1)) for f in a_data.get("uncertain_bars", [])}
    self_flags |= {int(f.get("bar", -1)) for f in b_data.get("uncertain_bars", [])}

    # Repair flagged bars (one batched call), budget permitting
    if merge.flagged and calls < cfg.max_calls_per_tune:
        try:
            fixed, c = _repair(cfg, client, unit, skeleton, merge, tiles)
            cost += c; calls += 1
            _apply_repairs(merge, fixed)
            debug["repair"] = fixed
        except (VLMRefusal, VLMTruncated) as exc:
            debug["repair_error"] = str(exc)

    body = assemble_body(skeleton, merge)
    flag_bars = sorted(set(merge.flagged) | {b for b in self_flags if b >= 0})
    abc_text = _finalize(skeleton, printed_key, body, flag_bars, merge)
    write_debug(cfg, unit.stem, "dual.json", debug)
    write_tune(cfg, unit.stem, abc_text)

    tune, report = validate_tune(abc_text, plan=skeleton.plan)
    if not report.ok:
        status = "flagged"
    elif flag_bars:
        status = "flagged"
    else:
        status = "ok"
    return DualOutcome(
        stem=unit.stem, status=status, abc_text=abc_text, report=report,
        calls=calls, cost=cost, printed_key=printed_key,
        agreement=merge.agreement, flagged=flag_bars,
        error="; ".join(str(e) for e in report.errors) if not report.ok else "")


def _assemble(skeleton: Skeleton, printed_key: str, data: dict) -> str:
    result = ReadResult(printed_key=printed_key,
                        abc_body=str(data.get("abc_body", "")),
                        flags=list(data.get("uncertain_bars", [])))
    return assemble_abc(skeleton, result)


def _finalize(skeleton: Skeleton, printed_key: str, body: str,
              flag_bars: list[int], merge: MergeResult) -> str:
    headers = []
    for line in skeleton.header_lines:
        headers.append(f"K:{printed_key}" if line.startswith("K:") and printed_key
                       else line)
    parts = ["\n".join(headers), body]
    reasons = {mb.global_no: mb.reason for mb in merge.bars if mb.reason}
    if flag_bars:
        parts.append("\n".join(
            f"% flag: {b} {reasons.get(b, 'uncertain')}".rstrip()
            for b in flag_bars))
    if merge.note:
        parts.append(f"% note: {merge.note}")
    return "\n".join(parts) + "\n"


def _repair(cfg: Config, client: VLMClient, unit: MelodyUnit,
            skeleton: Skeleton, merge: MergeResult, tiles) -> tuple[dict, float]:
    """Build and send one repair call for the flagged bars."""
    from .measure import barlines, heads
    from .strips import load_ink

    # map global bar number -> which system it is likely on (proportional)
    n_systems = len(build_tracks(unit.crop_path(cfg)))
    total_bars = max((mb.global_no for mb in merge.bars), default=1)
    _, ink = load_ink(unit.crop_path(cfg))

    flagged = [mb for mb in merge.bars if mb.flagged]
    items = []
    tiles_by_system: dict[int, tuple[str, str]] = {}
    # tiles are (label, b64, media) with 2 per system (left, right); use left
    for idx in range(0, len(tiles), 2):
        sysno = idx // 2 + 1
        tiles_by_system[sysno] = (tiles[idx][1], tiles[idx][2])
    for mb in flagged:
        sysno = min(n_systems, max(1, round(mb.global_no / total_bars * n_systems)))
        chord = skeleton.sections[mb.section_idx].chords
        bi = mb.global_no  # coarse; chord anchor lookup best-effort
        chord_text = ""
        sec = skeleton.sections[mb.section_idx]
        # section-local bar index for the chord anchor
        local = sum(1 for x in merge.bars
                    if x.section_idx == mb.section_idx and x.global_no <= mb.global_no)
        if 1 <= local <= len(sec.chords):
            chord_text = sec.chords[local - 1]
        items.append({
            "bar": mb.global_no, "system": sysno, "chord": chord_text or "-",
            "read_a": mb.read_a_src or "(none)", "read_b": mb.read_b_src or "(none)",
            "measure": "(see strip)", "reason": mb.reason,
        })
    content = build_repair_content(skeleton, items, tiles_by_system)
    data = client.repair(content)
    return data, usage_cost(cfg.model, client.last_usage)


def _apply_repairs(merge: MergeResult, fixed: dict) -> None:
    by_no = {mb.global_no: mb for mb in merge.bars}
    for item in fixed.get("bars", []):
        no = int(item.get("bar", -1))
        abc = str(item.get("abc", "")).strip()
        mb = by_no.get(no)
        if mb is None or not abc:
            continue
        mb.chosen_src = abc
        if item.get("confident", False):
            mb.flagged = False
            if no in merge.flagged:
                merge.flagged.remove(no)
            mb.reason = "repaired"
        else:
            mb.reason = "repair unsure"
