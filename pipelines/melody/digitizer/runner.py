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
from .output import ReadResult, assemble_abc, write_debug, write_tune
from .prompt import STRICTER_REMINDER, build_user_content
from .skeleton import build_skeleton
from .validation import Report, validate_tune
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
