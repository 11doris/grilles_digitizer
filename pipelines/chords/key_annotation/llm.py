"""Voter 2 — LLM key/fingerprint pass (tune_similarity_spec §3.3).

One structured-outputs call per tune, independent of the scorer (the prompt
never sees the scorer's answer). Interactive mode below 50 pending tunes,
Batches API at or above.
"""
from __future__ import annotations

import json
import time

import anthropic

MODEL = "claude-opus-4-8"
# The spec budgets 4000 output tokens for the JSON reply; adaptive thinking
# tokens also count against max_tokens, so give generous headroom — a
# truncated reply would flag the tune needs_review for no good reason.
MAX_TOKENS = 16000
BATCH_THRESHOLD = 50

_TONICS = ["C", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb",
           "B", "C#", "D#", "G#", "A#"]

_LOCAL_KEY_SCHEMA = {
    "anyOf": [
        {"type": "null"},
        {
            "type": "object",
            "properties": {
                "tonic": {"type": "string", "enum": _TONICS},
                "mode": {"type": "string", "enum": ["major", "minor"]},
            },
            "required": ["tonic", "mode"],
            "additionalProperties": False,
        },
    ]
}

# Structured-output schemas require additionalProperties: false and section
# names vary per tune, so `sections` is an array of {name, summary,
# local_key} objects (spec §3.3 note); it is converted to a keyed object
# when the annotated file is written.
KEY_SCHEMA = {
    "type": "object",
    "properties": {
        "tonic": {"type": "string", "enum": _TONICS},
        "mode": {"type": "string", "enum": ["major", "minor"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "modulation_note": {"type": ["string", "null"]},
        "fingerprint": {
            "type": "object",
            "properties": {
                "family": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "summary": {"type": "string"},
                            "local_key": _LOCAL_KEY_SCHEMA,
                        },
                        "required": ["name", "summary", "local_key"],
                        "additionalProperties": False,
                    },
                },
                "modulates": {"type": "boolean"},
            },
            "required": ["family", "tags", "sections", "modulates"],
            "additionalProperties": False,
        },
    },
    "required": ["tonic", "mode", "confidence", "modulation_note", "fingerprint"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are an expert jazz harmonist annotating chord charts transcribed from a
French anthology of jazz grilles (mostly swing-era and bebop standards).

For the tune you are given, determine:

1. **The key**: tonic and mode. Base your judgment primarily on the harmony
   (cadences, ii-V-I resolutions, where the form comes to rest), but
   recognizing the tune from its title/composer is legitimate evidence too.
   Watch out for these traps:
   - **Turnaround endings**: many charts end on a V7 or a ii chord so the
     form can loop; the key is where that turnaround resolves, not the final
     printed chord.
   - **Picardy thirds**: a minor tune may end on a major tonic chord; it is
     still a minor tune.
   - **Blues heads**: the tonic of a blues is often played as a dominant
     7th chord; that does not make the tune live a fourth away.
   - **Modulating tunes**: give the predominant/opening key as the tune key,
     set "modulates": true, describe the modulation briefly in
     "modulation_note" (e.g. "bridge in A"), and fill in local_key for the
     modulated sections (see below).

2. **Per-section local keys** (`local_key` inside each fingerprint section):
   null when the section sits in the global key. Fill it in ONLY when the
   section establishes a sustained local tonal center of its own — it cadences
   onto and dwells on another tonic for most of the section. Passing ii-Vs,
   secondary dominants and short tonicizations do NOT count. When in doubt,
   use null.

3. **A harmonic fingerprint**:
   - "family": a short label for the form/progression family, e.g.
     "32-bar AABA standard", "12-bar blues", "rhythm changes", "16-bar tune".
   - "tags": kebab-case descriptors of notable harmonic features. Prefer this
     vocabulary; add a new kebab-case tag only when nothing fits:
     blues-form, minor-blues, rhythm-changes-a, rhythm-changes-bridge,
     ii-V-chains, dominant-cycle-bridge, circle-of-fifths, turnaround-ending,
     tonic-pedal, chromatic-descent, modal, verse-present,
     montgomery-ward-bridge, sears-roebuck-bridge.
   - "sections": one entry per section (use the exact section names from the
     input, in the same order), each with a one-line summary of its harmonic
     content in roman-numeral terms (e.g. "I-vi-ii-V loop with a V/V in
     bar 3") and the local_key judgment described above.
   - "modulates": true only for a genuine change of tonal center between
     sections (matching your local_key / modulation_note answers).

Spell the tonic the way the chart itself spells its chords (prefer Bb, Eb,
Ab, Db, F# over their enharmonic twins unless the chart says otherwise).
Answer only via the required JSON schema.\
"""

_INPUT_FIELDS = ("title", "composer", "form", "time_signature", "sections")


def user_payload(tune: dict) -> str:
    """The tune JSON, trimmed to the fields that inform the key (spec §3.3)."""
    return json.dumps({k: tune[k] for k in _INPUT_FIELDS if k in tune},
                      ensure_ascii=False, indent=1)


def request_params(tune: dict) -> dict:
    """messages.create kwargs for one tune — shared by both run modes."""
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        "output_config": {"format": {"type": "json_schema", "schema": KEY_SCHEMA}},
        "messages": [{"role": "user", "content": user_payload(tune)}],
    }


class LLMVoteError(Exception):
    """The LLM pass failed for one tune (recorded, never crashes the run)."""


def _parse_response(message) -> dict:
    if message.stop_reason == "refusal":
        raise LLMVoteError("model refused the request")
    if message.stop_reason == "max_tokens":
        raise LLMVoteError("reply truncated at max_tokens")
    text = next((b.text for b in message.content if b.type == "text"), None)
    if text is None:
        raise LLMVoteError("no text block in reply")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMVoteError(f"schema-violating reply: {exc}") from exc


# Retry discipline mirrors pipelines/chords/digitizer/vlm.py.
_TRANSIENT_BACKOFF = (1.0, 2.0, 4.0)


def _call_with_backoff(client: anthropic.Anthropic, params: dict):
    last_exc: Exception | None = None
    for attempt in range(len(_TRANSIENT_BACKOFF) + 1):
        try:
            return client.messages.create(**params)
        except (anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError) as exc:
            last_exc = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500 and exc.status_code != 429:
                raise  # non-transient client error, fatal for this tune
            last_exc = exc
        if attempt < len(_TRANSIENT_BACKOFF):
            time.sleep(_TRANSIENT_BACKOFF[attempt])
    assert last_exc is not None
    raise last_exc


def _one_interactive(client: anthropic.Anthropic, tune: dict
                     ) -> dict | LLMVoteError:
    try:
        return _parse_response(_call_with_backoff(client, request_params(tune)))
    except LLMVoteError as exc:
        return exc
    except anthropic.APIStatusError as exc:
        return LLMVoteError(f"API error {exc.status_code}: {exc.message}")


def run_interactive(client: anthropic.Anthropic, tunes: dict[str, dict],
                    progress=print, workers: int = 1
                    ) -> dict[str, dict | LLMVoteError]:
    """messages.create calls, optionally a few in parallel (workers > 1)."""
    results: dict[str, dict | LLMVoteError] = {}
    if workers <= 1:
        for i, (stem, tune) in enumerate(tunes.items(), 1):
            progress(f"  [{i}/{len(tunes)}] LLM: {stem}")
            results[stem] = _one_interactive(client, tune)
        return results

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one_interactive, client, tune): stem
                   for stem, tune in tunes.items()}
        for i, fut in enumerate(as_completed(futures), 1):
            stem = futures[fut]
            results[stem] = fut.result()
            progress(f"  [{i}/{len(tunes)}] LLM done: {stem}")
    return results


def run_batch(client: anthropic.Anthropic, tunes: dict[str, dict],
              progress=print, poll_seconds: float = 15.0
              ) -> dict[str, dict | LLMVoteError]:
    """Batches API run — 50% price, used for large pending sets (spec §3.3)."""
    requests = [{"custom_id": stem, "params": request_params(tune)}
                for stem, tune in tunes.items()]
    batch = client.messages.batches.create(requests=requests)
    progress(f"  batch {batch.id} submitted ({len(requests)} tunes); polling ...")

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        progress(f"  batch {batch.id}: {batch.processing_status}"
                 f" (processing={counts.processing} succeeded={counts.succeeded}"
                 f" errored={counts.errored})")
        time.sleep(poll_seconds)

    results: dict[str, dict | LLMVoteError] = {
        stem: LLMVoteError("missing from batch results") for stem in tunes}
    for result in client.messages.batches.results(batch.id):
        stem = result.custom_id
        kind = result.result.type
        if kind == "succeeded":
            try:
                results[stem] = _parse_response(result.result.message)
            except LLMVoteError as exc:
                results[stem] = exc
        else:
            detail = ""
            if kind == "errored":
                detail = f": {result.result.error}"
            results[stem] = LLMVoteError(f"batch request {kind}{detail}")
    return results


def run(tunes: dict[str, dict], progress=print, *,
        force_interactive: bool = False, workers: int = 1
        ) -> dict[str, dict | LLMVoteError]:
    """Dispatch on the pending count (spec §3.3 run modes).

    `force_interactive` skips the Batches API regardless of size — useful when
    results are wanted now and batch scheduling latency is not.
    """
    if not tunes:
        return {}
    client = anthropic.Anthropic()
    if len(tunes) >= BATCH_THRESHOLD and not force_interactive:
        return run_batch(client, tunes, progress)
    return run_interactive(client, tunes, progress, workers=workers)
