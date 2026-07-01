"""Batch orchestrator: discovery, resume, per-unit retries, state, and report."""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic

from . import output
from .config import Config, SOURCE_CONSTANT
from .images import prepare_crop
from .manifest import WorkUnit, load_manifest
from .prompt import STRICTER_REMINDER, build_user_content
from .validation import ValidationError, parse_json, review_flags, validate
from .vlm import MissingCredentials, VLMClient, VLMRefusal


@dataclass
class UnitResult:
    unit: WorkUnit
    status: str  # "ok" | "skipped" | "error"
    attempts: int
    review_flags: dict[str, bool] = field(default_factory=dict)
    last_error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_CASE_FIELDS = ("title", "composer", "style", "tempo")


def _title_case(value: str) -> str:
    """Capitalize the first letter of each space-delimited word, lower-casing the
    rest. Applied only to fully upper-case values (see `_normalize_case`), so the
    " – " composer separator and any already-cased text are left intact."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in value.split(" "))


def _normalize_case(obj: dict) -> None:
    """Render the display fields in Title Case rather than the book's all-caps.
    Only all-upper-case values are converted, so a value the model already cased
    correctly (e.g. a composer's name with internal capitals) is preserved."""
    for key in _CASE_FIELDS:
        value = obj.get(key)
        if isinstance(value, str) and value.isupper():
            obj[key] = _title_case(value)


def _select(units: list[WorkUnit], config: Config) -> list[WorkUnit]:
    if config.only:
        units = [u for u in units if u.current_file == config.only]
    if config.page_range:
        lo, hi = config.page_range
        units = [u for u in units if lo <= u.page <= hi]
    return units


def _already_valid(config: Config, unit: WorkUnit) -> bool:
    """Resume rule: a unit is done iff its JSON exists and still validates."""
    path = output.json_path(config, unit)
    if not path.exists():
        return False
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        validate(obj, unit)
        return True
    except (ValidationError, json.JSONDecodeError, OSError):
        return False


def _transcribe_unit(config: Config, client: VLMClient, unit: WorkUnit) -> UnitResult:
    """Run one work unit: clean image, then call+validate with per-unit retries."""
    crop_path = config.crops_dir / unit.current_file
    image_b64, media_type = prepare_crop(
        crop_path, dilate=config.dilate, max_long_edge=config.max_long_edge
    )
    user_content = build_user_content(unit, image_b64, media_type)

    last_error = ""
    last_raw = ""
    for attempt in range(1, config.retries + 1):
        reminder = STRICTER_REMINDER * (attempt - 1)  # progressively stricter
        try:
            raw = client.transcribe(user_content, extra_reminder=reminder)
            last_raw = raw
            obj = parse_json(raw)
            # The runner owns title/page/source (spec §5) — inject before validating
            # so those always-present fields can never be missing.
            obj["title"] = unit.title
            obj["page"] = unit.page
            obj["source"] = SOURCE_CONSTANT
            _normalize_case(obj)
            validate(obj, unit)
        except (ValidationError, VLMRefusal) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        output.write_tune(config, unit, obj)
        return UnitResult(unit, "ok", attempt, review_flags(obj, unit))

    output.write_error_stub(
        config, unit, attempts=config.retries, last_error=last_error, raw_excerpt=last_raw
    )
    return UnitResult(unit, "error", config.retries, last_error=last_error)


class _Report:
    """Accumulates run results; thread-safe for the worker pool."""

    def __init__(self, total: int, model: str):
        self.total = total
        self.model = model
        self.succeeded = 0
        self.failed = 0
        self.skipped = 0
        self.flagged = {
            "missing_required_field": [],
            "no_chord_grid": [],
            "errors": [],
        }
        self._lock = threading.Lock()
        self._done = 0

    def record(self, result: UnitResult, state_fh) -> int:
        """Update counters, append the state line, and return the running index."""
        with self._lock:
            if result.status == "ok":
                self.succeeded += 1
                f = result.review_flags
                if f.get("missing_required_field"):
                    self.flagged["missing_required_field"].append(
                        result.unit.current_file
                    )
                if f.get("no_chord_grid"):
                    self.flagged["no_chord_grid"].append(result.unit.current_file)
            elif result.status == "skipped":
                self.skipped += 1
            else:
                self.failed += 1
                self.flagged["errors"].append(result.unit.current_file)

            state_fh.write(
                json.dumps(
                    {
                        "current_file": result.unit.current_file,
                        "status": result.status,
                        "attempts": result.attempts,
                        "ts": _now(),
                    }
                )
                + "\n"
            )
            state_fh.flush()
            self._done += 1
            return self._done

    def summary(self, elapsed_s: float) -> dict:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "skipped": self.skipped,
            "failed": self.failed,
            "elapsed_s": round(elapsed_s, 1),
            "model": self.model,
            "flagged": self.flagged,
        }


def _log(msg: str) -> None:
    print(msg, flush=True)


def run(config: Config) -> dict:
    units = _select(load_manifest(config.manifest), config)
    total = len(units)
    report = _Report(total, config.model)
    client = VLMClient(config)
    start = time.monotonic()

    _log(
        f"grilles_digitizer | model={config.model} workers={config.workers} "
        f"retries={config.retries} dilate={config.dilate} "
        f"max_long_edge={config.max_long_edge} max_output_tokens={config.max_output_tokens}"
    )
    _log(f"crops={config.crops_dir}  out={config.out_dir}  units={total}")

    if total == 0:
        hint = ""
        if config.only:
            hint = f" (--only {config.only!r} matched no manifest row)"
        elif config.page_range:
            hint = f" (--page-range {config.page_range[0]}:{config.page_range[1]} matched no rows)"
        _log(f"nothing to do: 0 work units{hint}")

    config.out_dir.mkdir(parents=True, exist_ok=True)

    def process(unit: WorkUnit) -> UnitResult:
        if _already_valid(config, unit):
            return UnitResult(unit, "skipped", 0)
        try:
            result = _transcribe_unit(config, client, unit)
        except (
            MissingCredentials,
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
        ):
            raise  # a bad/absent key dooms every unit — fail fast, don't churn stubs
        except Exception as exc:  # error isolation — one unit never stops the batch
            if config.debug:
                traceback.print_exc()
            output.write_error_stub(
                config, unit, attempts=0, last_error=f"{type(exc).__name__}: {exc}",
                raw_excerpt="",
            )
            result = UnitResult(unit, "error", 0, last_error=f"{type(exc).__name__}: {exc}")
        if config.delay and result.status != "skipped":
            time.sleep(config.delay)
        return result

    def emit(result: UnitResult, state_fh) -> None:
        done = report.record(result, state_fh)
        pct = int(done / total * 100) if total else 100
        if result.status == "skipped":
            note = "(cached)"
        else:
            note = f"({result.attempts} attempt{'s' if result.attempts != 1 else ''})"
        title = result.unit.title[:40]
        line = (
            f"[{pct:3d}% | {done}/{total}] page {result.unit.page}  {title}  "
            f"-> {result.status} {note}"
        )
        if result.status == "error" and result.last_error:
            line += f"  reason: {result.last_error[:160]}"
        _log(line)

    with open(config.state_path, "a", encoding="utf-8") as state_fh:
        if config.workers > 1:
            with ThreadPoolExecutor(max_workers=config.workers) as pool:
                for result in pool.map(process, units):
                    emit(result, state_fh)
        else:
            for unit in units:
                emit(process(unit), state_fh)

    summary = report.summary(time.monotonic() - start)
    config.report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _log(
        f"done: {summary['succeeded']} ok, {summary['skipped']} skipped, "
        f"{summary['failed']} failed in {summary['elapsed_s']}s -> {config.report_path}"
    )
    if summary["failed"]:
        _log(
            f"  {summary['failed']} failed - see per-tune reasons above, the "
            f".error.json stubs in {config.out_dir}, or re-run with --debug for tracebacks."
        )
    return summary
