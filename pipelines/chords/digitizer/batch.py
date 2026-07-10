"""Batches API mode for stage 2 (spec §18 cost lever): at or above
BATCH_THRESHOLD pending crops, the transcription calls are submitted as
message batches at 50% of interactive price and polled until done.

Durability contract (mirrors key_annotation/llm.py): every batch id is
persisted to `<out_dir>/batch_state.json` the moment the batch is created,
and the file is deleted only after all results have been fetched, so an
interrupted run never orphans a paid batch — simply re-running
`transcribe.py` picks it up. Each fetched reply goes through the exact same
acceptance path as interactive mode (`runner._accept`: parse, inject,
canonicalize, validate, atomic write); a reply that fails is left for the
interactive retry ladder, which the runner applies after the batch phase.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import anthropic

from .config import Config
from .images import prepare_crop
from .manifest import WorkUnit, load_units
from .prompt import build_user_content
from .validation import ValidationError
from .vlm import (
    _AUTH_RESOLVE_MARKER, MissingCredentials, VLMRefusal, VLMTruncated,
    build_request_kwargs, extract_tool_json,
)

BATCH_THRESHOLD = 50       # pending units at/above which batch mode kicks in
MAX_BATCH_REQUESTS = 500   # chunk size: keeps each batch's base64 payload small
POLL_SECONDS = 30.0

_TRANSIENT_BACKOFF = (1.0, 2.0, 4.0)


def state_path(config: Config) -> Path:
    return config.out_dir / "batch_state.json"


def load_state(config: Config) -> dict | None:
    path = state_path(config)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if state.get("batches") else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(config: Config, state: dict) -> None:
    state_path(config).write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _api_call(fn, *args, **kwargs):
    """Transient-error backoff + the credentials fail-fast from vlm.py."""
    last_exc: Exception | None = None
    for attempt in range(len(_TRANSIENT_BACKOFF) + 1):
        try:
            return fn(*args, **kwargs)
        except TypeError as exc:
            if _AUTH_RESOLVE_MARKER in str(exc):
                raise MissingCredentials(
                    "No Anthropic credentials found. Set ANTHROPIC_API_KEY "
                    "(or ANTHROPIC_AUTH_TOKEN, or run `ant auth login`)."
                ) from exc
            raise
        except (anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError) as exc:
            last_exc = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500 and exc.status_code != 429:
                raise
            last_exc = exc
        if attempt < len(_TRANSIENT_BACKOFF):
            time.sleep(_TRANSIENT_BACKOFF[attempt])
    assert last_exc is not None
    raise last_exc


def _request_for(config: Config, unit: WorkUnit) -> dict:
    image_b64, media_type = prepare_crop(
        config.crops_dir / unit.current_file,
        dilate=config.dilate, max_long_edge=config.max_long_edge)
    return build_request_kwargs(
        config, build_user_content(unit, image_b64, media_type))


def _submit(config: Config, api, pending: list[WorkUnit], log) -> dict:
    """Create the batch(es) for `pending`; the state file is (re)written
    after every single create so no batch id can ever be lost."""
    # custom_id must be short and [A-Za-z0-9_-]; stems can exceed the limit,
    # so requests are numbered and the mapping stored in the state file.
    requests = []
    mapping: dict[str, str] = {}
    for i, unit in enumerate(pending):
        cid = f"u{i:05d}"
        mapping[cid] = unit.current_file
        requests.append({"custom_id": cid, "params": _request_for(config, unit)})

    state = {
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "batches": [],
        "units": mapping,
    }
    config.out_dir.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(requests), MAX_BATCH_REQUESTS):
        chunk = requests[start:start + MAX_BATCH_REQUESTS]
        batch = _api_call(api.messages.batches.create, requests=chunk)
        state["batches"].append(batch.id)
        _write_state(config, state)
        log(f"  batch {batch.id} submitted ({len(chunk)} crops)")
    return state


def _poll(api, batch_ids: list[str], log) -> None:
    """Block until every batch has ended; transient poll errors just retry."""
    unended = list(batch_ids)
    try:
        while unended:
            still = []
            processing = succeeded = errored = 0
            for bid in unended:
                try:
                    batch = api.messages.batches.retrieve(bid)
                except (anthropic.APIConnectionError,
                        anthropic.APIStatusError) as exc:
                    log(f"  batch {bid}: poll failed ({exc}); retrying")
                    still.append(bid)
                    continue
                if batch.processing_status != "ended":
                    still.append(bid)
                counts = batch.request_counts
                processing += counts.processing
                succeeded += counts.succeeded
                errored += counts.errored
            unended = still
            if unended:
                log(f"  {len(unended)} batch(es) running: "
                    f"processing={processing} succeeded={succeeded} "
                    f"errored={errored}")
                time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log("\n  interrupted — the batch keeps running server-side and its id "
            "is saved; just re-run transcribe.py later to fetch the results")
        raise


def run_batch(config: Config, api, pending: list[WorkUnit], log
              ) -> tuple[dict, set[str]]:
    """Submit (or resume) a batch run and fetch its results.

    Returns (accepted, attempted): accepted results as
    {current_file: UnitResult}, and the set of files this batch covered.
    Attempted-but-not-accepted units (batch errors, rejected replies) must go
    to the interactive retry ladder, never into another batch — resubmitting
    the identical request would just fail the same way at half price twice.
    """
    from . import runner  # late import (runner imports this module lazily)

    state = load_state(config)
    if state is None:
        log(f"batch mode: submitting {len(pending)} crops "
            "(50% price; force per-call mode with --interactive)")
        state = _submit(config, api, pending, log)
    else:
        log(f"resuming {len(state['batches'])} unfinished batch(es) "
            f"from {state_path(config).name}")

    _poll(api, state["batches"], log)

    # Units are resolved from the full crop set, not this run's selection, so
    # results from a batch submitted under different filters are still
    # written (the output file is the durable artifact either way).
    units_by_file = {u.current_file: u
                     for u in load_units(config.crops_dir, config.manifest)}
    results: dict[str, runner.UnitResult] = {}
    accepted = failed = 0
    for bid in state["batches"]:
        for item in _api_call(api.messages.batches.results, bid):
            current_file = state["units"].get(item.custom_id)
            unit = units_by_file.get(current_file)
            if unit is None:
                log(f"  batch result for unknown crop {current_file!r} "
                    "(renamed/removed since submission) — skipped")
                continue
            if item.result.type != "succeeded":
                detail = ""
                if item.result.type == "errored":
                    detail = f": {item.result.error}"
                log(f"  {current_file}: batch request {item.result.type}"
                    f"{detail} (will retry interactively)")
                failed += 1
                continue
            try:
                raw = extract_tool_json(item.result.message)
                results[current_file] = runner._accept(config, unit, raw,
                                                       attempts=1)
                accepted += 1
            except (ValidationError, VLMRefusal, VLMTruncated) as exc:
                log(f"  {current_file}: batch reply rejected "
                    f"({type(exc).__name__}: {exc}) — will retry interactively")
                failed += 1

    state_path(config).unlink(missing_ok=True)
    log(f"batch phase done: {accepted} accepted, {failed} for interactive retry")
    return results, set(state["units"].values())
