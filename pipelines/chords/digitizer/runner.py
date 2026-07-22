"""Batch orchestrator: discovery, resume, per-unit retries, state, and report."""

from __future__ import annotations

import json
import random
import re
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
from .manifest import WorkUnit, load_units
from .prompt import SPILL_RECHECK_REMINDER, STRICTER_REMINDER, build_user_content
from .validation import (
    ValidationError, parse_json, review_flags, validate, variant_spills)
from .vlm import MissingCredentials, VLMClient, VLMRefusal, VLMTruncated


@dataclass
class UnitResult:
    unit: WorkUnit
    status: str  # "ok" | "skipped" | "error"
    attempts: int
    review_flags: dict[str, bool] = field(default_factory=dict)
    last_error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Deterministic book-notation -> canonical conversions (spec §12). The model is
# asked to apply these itself, but occasionally leaves one in place (e.g. 'Db7M'),
# which `validate()` then rejects, burning every retry. Applying the unambiguous
# conversions here as well makes the pipeline robust to that. Order matters: the
# minor-major forms (mM7 / m7M) must be handled before the plain major-7 rule so
# they map to 'm(maj7)' rather than 'mmaj7'. The contextual repeat/dash shorthand
# (%, →, •, -) is deliberately left untouched — it still forces a retry.
_MINMAJ7_RE = re.compile(r"m(?:M7|7M)")  # mM7 / m7M      -> m(maj7)
_MAJ7_RE = re.compile(r"7M|M7|[Δ△]")     # 7M / M7 / Δ / △ -> maj7  (also m(M7))
_HALFDIM_RE = re.compile(r"[øØ]")        # ø / Ø          -> m7b5


def _canonicalize_chord(chord: str) -> str:
    chord = _MINMAJ7_RE.sub("m(maj7)", chord)
    chord = _MAJ7_RE.sub("maj7", chord)
    chord = _HALFDIM_RE.sub("m7b5", chord)
    return chord.replace("/14", "#11")


def _canonicalize_chords(obj: dict) -> None:
    """Rewrite every beat's chord string into the canonical vocabulary in place.
    Defensive about structure — `validate()` reports any malformed sections."""
    sections = obj.get("sections")
    if not isinstance(sections, dict):
        return
    for bars in sections.values():
        if not isinstance(bars, list):
            continue
        for bar in bars:
            beats = bar.get("beats") if isinstance(bar, dict) else None
            if not isinstance(beats, dict):
                continue
            for key, chord in beats.items():
                if isinstance(chord, str):
                    beats[key] = _canonicalize_chord(chord)


def _select(units: list[WorkUnit], config: Config) -> list[WorkUnit]:
    if config.only:
        units = [u for u in units if u.current_file == config.only]
    if config.files is not None:
        wanted = set(config.files)
        units = [u for u in units if u.current_file in wanted]
        missing = wanted - {u.current_file for u in units}
        if missing:
            print(
                f"warning: {len(missing)} name(s) in --files not found in "
                f"{config.crops_dir}: {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
    if config.page_range:
        lo, hi = config.page_range
        units = [u for u in units if lo <= u.page <= hi]
    if config.sample is not None:
        # Keep only crops not yet decoded into out_dir, then randomly pick N.
        todo = [
            u for u in units
            if not (config.out_dir / f"{u.stem}.json").exists()
        ]
        rng = random.Random(config.seed)
        units = rng.sample(todo, min(config.sample, len(todo)))
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


def _prepare(config: Config, unit: WorkUnit, raw: str) -> dict:
    """Parse a reply into a validated tune object WITHOUT writing it: parse,
    inject the runner-owned fields (title/page/source, spec §5, so those
    always-present fields can never be missing), canonicalize, validate.
    Raises ValidationError when the reply doesn't hold up. Splitting this out
    from the write lets a caller inspect the object first (e.g. the batch phase
    defers a cross-section variant spill to the interactive double-check)."""
    obj = parse_json(raw)
    obj["title"] = unit.title
    obj["page"] = unit.page
    obj["source"] = SOURCE_CONSTANT
    _canonicalize_chords(obj)
    validate(obj, unit)
    return obj


def _write_accepted(config: Config, unit: WorkUnit, obj: dict, attempts: int) -> UnitResult:
    """Persist an already-validated object and build its ok result."""
    output.write_tune(config, unit, obj)
    return UnitResult(unit, "ok", attempts, review_flags(obj, unit))


def _accept(config: Config, unit: WorkUnit, raw: str, attempts: int) -> UnitResult:
    """Shared acceptance path for interactive and batch replies: `_prepare`
    then write. Raises ValidationError when the reply doesn't hold up."""
    return _write_accepted(config, unit, _prepare(config, unit, raw), attempts)


def _transcribe_unit(config: Config, client: VLMClient, unit: WorkUnit) -> UnitResult:
    """Run one work unit: clean image, then call+validate with per-unit retries."""
    crop_path = config.crops_dir / unit.current_file
    image_b64, media_type = prepare_crop(
        crop_path, dilate=config.dilate, max_long_edge=config.max_long_edge
    )
    user_content = build_user_content(unit, image_b64, media_type)

    last_error = ""
    last_raw = ""
    max_tokens = config.max_output_tokens
    recheck_spill = False   # ask the model to re-verify on the NEXT call
    rechecked = False       # the one-shot double-check has already been spent
    fallback: dict | None = None  # last validated obj, kept across the recheck call
    for attempt in range(1, config.retries + 1):
        reminder = STRICTER_REMINDER * (attempt - 1)  # progressively stricter
        if recheck_spill:
            reminder += SPILL_RECHECK_REMINDER
            recheck_spill = False
        try:
            raw = client.transcribe(user_content, extra_reminder=reminder,
                                    max_tokens=max_tokens)
            last_raw = raw
            obj = _prepare(config, unit, raw)
        except VLMTruncated as exc:
            # Dense multi-strain grids overflow the default cap. Retry at a
            # doubled cap (output is billed by use, not by the cap) instead
            # of burning an error stub that would need a manual
            # --max-output-tokens rerun.
            last_error = f"{type(exc).__name__}: {exc}"
            max_tokens = min(max_tokens * 2, 32000)
            continue
        except (ValidationError, VLMRefusal) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        # Guard: a variant whose boxes cross a section border is legal but rare.
        # Spend exactly one retry asking the model to double-check that reading
        # against the image before accepting it; if it repeats (or attempts run
        # out) we take the result as-is.
        spills = variant_spills(obj)
        if spills and not rechecked and attempt < config.retries:
            rechecked = True
            recheck_spill = True
            fallback = obj  # keep in case the re-verify call fails to validate
            last_error = "cross-section variant spill; re-verifying: " + "; ".join(spills)
            continue
        return _write_accepted(config, unit, obj, attempt)

    # Retries exhausted. If the double-check call was the thing that failed, we
    # still hold the earlier valid transcription — accept it rather than binning
    # a good tune over an unconfirmed (but legal) spill.
    if fallback is not None:
        return _write_accepted(config, unit, fallback, config.retries)

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
    units = _select(load_units(config.crops_dir, config.manifest), config)
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
            hint = f" (--only {config.only!r} matched no crop in {config.crops_dir})"
        elif config.page_range:
            hint = f" (--page-range {config.page_range[0]}:{config.page_range[1]} matched no rows)"
        _log(f"nothing to do: 0 work units{hint}")

    config.out_dir.mkdir(parents=True, exist_ok=True)

    # Batch phase (spec §18 cost lever): resume any unfinished batch first,
    # then submit a new one when this run's pending set is large enough. Two
    # rounds cover the case where a resumed batch was submitted under a
    # different selection and today's pending set still qualifies. Accepted
    # results are written to disk inside the batch phase; everything else
    # (batch errors, rejected replies) falls through to the interactive
    # retry ladder below.
    from . import batch as batch_mod
    batch_results: dict[str, UnitResult] = {}
    batch_attempted: set[str] = set()
    for _ in range(2):
        pending = [u for u in units
                   if u.current_file not in batch_attempted
                   and not _already_valid(config, u)]
        below_threshold = len(pending) < batch_mod.BATCH_THRESHOLD
        if batch_mod.load_state(config) is None and (
                config.interactive or not pending
                or (below_threshold and not config.force_batch)):
            break
        accepted, attempted = batch_mod.run_batch(
            config, client.api, pending, _log)
        batch_results.update(accepted)
        batch_attempted |= attempted

    def process(unit: WorkUnit) -> UnitResult:
        pre = batch_results.pop(unit.current_file, None)
        if pre is not None:
            return pre
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
