"""Adjudication, annotated-file I/O and the shared update routine (spec §3.1/§3.5).

`05_annotated` files are verbatim copies of their `04_verified` source with
`key`, `section_keys`, `opening`, `key_annotation` and `harmonic_fingerprint`
added; source fields are never altered. Files must never be edited by hand —
every correction goes through `update_annotation`, which recomputes the
derived fields.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pipelines.chords.similarity.normalize import PC_NAME, compute_opening, pitch_class
from .llm import LLMVoteError
from .scorer import TUNE_MARGIN_THRESHOLD, KeyVote, section_local_keys

STATUS_AGREED = "agreed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_VERIFIED = "verified"

_ANNOTATION_FIELDS = ("key", "section_keys", "opening", "key_annotation",
                      "harmonic_fingerprint")


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_tonic(tonic: str) -> str:
    """Spell a tonic in the book's vocabulary (Bb/Eb/Ab/Db/F#, spec §3.1)."""
    return PC_NAME[pitch_class(tonic)]


def _same_key(a: dict, b: dict) -> bool:
    return (pitch_class(a["tonic"]) == pitch_class(b["tonic"])
            and a["mode"] == b["mode"])


def _clean_section_keys(section_keys: dict | None, key: dict) -> dict:
    """Canonical spelling; entries equal to the global key are dropped."""
    out = {}
    for name, local in (section_keys or {}).items():
        if _same_key(local, key):
            continue
        out[name] = {"tonic": canonical_tonic(local["tonic"]), "mode": local["mode"]}
    return out


def llm_vote_json(llm: dict | LLMVoteError) -> dict:
    """The `key_annotation.llm` record: the vote as cast, or the failure."""
    if isinstance(llm, LLMVoteError):
        return {"error": str(llm)}
    vote = {"tonic": llm["tonic"], "mode": llm["mode"],
            "confidence": llm["confidence"],
            "modulation_note": llm["modulation_note"]}
    section_keys = llm_section_keys(llm)
    if section_keys:
        vote["section_keys"] = section_keys
    return vote


def llm_section_keys(llm: dict) -> dict:
    """{section: {tonic, mode}} from the fingerprint's local_key entries."""
    out = {}
    for sec in llm["fingerprint"]["sections"]:
        if sec.get("local_key"):
            out[sec["name"]] = {"tonic": sec["local_key"]["tonic"],
                                "mode": sec["local_key"]["mode"]}
    return out


def fingerprint_json(llm: dict) -> dict:
    """`harmonic_fingerprint`: sections array converted to a keyed object."""
    fp = llm["fingerprint"]
    out = {
        "family": fp["family"],
        "tags": fp["tags"],
        "sections": {sec["name"]: sec["summary"] for sec in fp["sections"]},
        "modulates": fp["modulates"],
    }
    if llm.get("modulation_note"):
        out["modulation_note"] = llm["modulation_note"]
    return out


def _adjudicate(scorer: KeyVote, llm: dict | LLMVoteError
                ) -> tuple[str, list[str]]:
    """(status, review_reasons) from the two votes (spec §3.5)."""
    reasons: list[str] = []
    if isinstance(llm, LLMVoteError):
        reasons.append(f"llm pass failed: {llm}")
        return STATUS_NEEDS_REVIEW, reasons

    scorer_key = {"tonic": scorer.tonic, "mode": scorer.mode}
    llm_key = {"tonic": llm["tonic"], "mode": llm["mode"]}
    if not _same_key(scorer_key, llm_key):
        reasons.append(
            f"key disagreement: scorer {scorer.tonic} {scorer.mode}"
            f" (margin {scorer.margin:.2f}) vs llm {llm['tonic']} {llm['mode']}"
            f" ({llm['confidence']} confidence)")
    if scorer.margin < TUNE_MARGIN_THRESHOLD:
        reasons.append(f"thin scorer margin: {scorer.margin:.2f}"
                       f" < {TUNE_MARGIN_THRESHOLD}")

    s_sections = {n: {"tonic": d["tonic"], "mode": d["mode"]}
                  for n, d in scorer.section_keys.items()}
    l_sections = llm_section_keys(llm)
    for name in sorted(set(s_sections) | set(l_sections)):
        a, b = s_sections.get(name), l_sections.get(name)
        if a is None or b is None or not _same_key(a, b):
            def fmt(k):
                return f"{k['tonic']} {k['mode']}" if k else "no local key"
            reasons.append(f"section {name!r} local-key disagreement:"
                           f" scorer {fmt(a)} vs llm {fmt(b)}")

    return (STATUS_NEEDS_REVIEW if reasons else STATUS_AGREED), reasons


def build_annotation(source: dict, sha256: str, scorer: KeyVote,
                     llm: dict | LLMVoteError) -> dict:
    """Assemble a fresh 05_annotated document from the two votes."""
    status, reasons = _adjudicate(scorer, llm)

    # Resolved (or, under needs_review, provisional) key: on agreement the
    # votes coincide; on disagreement prefer the LLM vote for the provisional
    # display — recognizing the standard usually beats chord statistics on
    # exactly the charts that end up here — the scorer vote stays alongside.
    if isinstance(llm, LLMVoteError):
        key = {"tonic": canonical_tonic(scorer.tonic), "mode": scorer.mode}
        section_keys = _clean_section_keys(
            {n: d for n, d in scorer.section_keys.items()}, key)
    else:
        key = {"tonic": canonical_tonic(llm["tonic"]), "mode": llm["mode"]}
        section_keys = _clean_section_keys(llm_section_keys(llm), key)

    annotated = dict(source)  # verbatim copy, annotation fields appended
    annotated["key"] = key
    if section_keys:
        annotated["section_keys"] = section_keys
    annotated["opening"] = compute_opening(source, key["tonic"], key["mode"])
    annotation = {
        "status": status,
        "source_sha256": sha256,
        "scorer": scorer.to_json(),
        "llm": llm_vote_json(llm),
    }
    if reasons:
        annotation["review_reasons"] = reasons
    annotated["key_annotation"] = annotation
    if not isinstance(llm, LLMVoteError):
        annotated["harmonic_fingerprint"] = fingerprint_json(llm)
    return annotated


# ---------------------------------------------------------------------------
# Shared update routine — the only legal way to change an annotated file
# (used by the key verifier app's save and by `annotate_keys.py --set-key`).
# ---------------------------------------------------------------------------

_UNSET = object()


def update_annotation(annotated: dict, *, tonic: str | None = None,
                      mode: str | None = None, section_keys=_UNSET,
                      fingerprint=_UNSET) -> dict:
    """Apply a human verification/correction (spec §3.5).

    Applies the new key (or keeps the current one), recomputes every derived
    field (`opening`, `section_keys` consistency), preserves the original
    voter votes, and sets status `verified` with `human.corrected` reflecting
    whether the key or section keys actually changed. Returns `annotated`
    (mutated in place).

    §3.5 staleness handling: when the key or section keys actually changed
    and the human did not edit the fingerprint in the same save, the
    fingerprint (whose prose was written under the old key) is flagged
    `stale: true` for the key-pinned LLM refresh on the next
    `annotate_keys.py` run. On a key change the deterministic per-section
    pass is re-run under the corrected key; newly detected local keys are
    stored as `key_annotation.section_key_proposals` for the verifier app to
    accept or dismiss — never silently written into `section_keys`.
    """
    old_key = dict(annotated["key"])
    old_sections = dict(annotated.get("section_keys") or {})

    key = {
        "tonic": canonical_tonic(tonic if tonic is not None else old_key["tonic"]),
        "mode": mode if mode is not None else old_key["mode"],
    }
    new_sections = _clean_section_keys(
        old_sections if section_keys is _UNSET else (section_keys or {}), key)

    annotated["key"] = key
    if new_sections:
        annotated["section_keys"] = new_sections
    else:
        annotated.pop("section_keys", None)
    annotated["opening"] = compute_opening(annotated, key["tonic"], key["mode"])

    # The verifier app posts the fingerprint on every save, edited or not —
    # "edited" therefore means the merge actually changed something, not that
    # the field was present in the request.
    fingerprint_edited = False
    if fingerprint is not _UNSET and fingerprint is not None:
        current = annotated.get("harmonic_fingerprint") or {}
        before = {k: v for k, v in current.items() if k != "stale"}
        for field in ("family", "tags", "sections", "modulates", "modulation_note"):
            if field in fingerprint:
                current[field] = fingerprint[field]
        if current.get("modulation_note") in ("", None):
            current.pop("modulation_note", None)
        annotated["harmonic_fingerprint"] = current
        fingerprint_edited = (
            {k: v for k, v in current.items() if k != "stale"} != before)

    key_changed = not _same_key(key, old_key)
    corrected = key_changed or _clean_section_keys(old_sections, key) != new_sections

    fp = annotated.get("harmonic_fingerprint")
    if fp is not None:
        if fingerprint_edited:
            fp.pop("stale", None)
        elif corrected:
            fp["stale"] = True

    annotation = annotated["key_annotation"]
    if key_changed:
        proposals = {
            name: local for name, local
            in section_local_keys(annotated, key["tonic"], key["mode"]).items()
            if name not in new_sections}
        if proposals:
            annotation["section_key_proposals"] = proposals
        else:
            annotation.pop("section_key_proposals", None)
    else:
        # No key change: any pending proposals were on screen in this save —
        # the human accepted them (via section_keys) or is dismissing them.
        annotation.pop("section_key_proposals", None)

    annotation["status"] = STATUS_VERIFIED
    annotation["human"] = {"tonic": key["tonic"], "mode": key["mode"],
                           "corrected": corrected}
    annotation.pop("review_reasons", None)
    return annotated


# ---------------------------------------------------------------------------
# File I/O and pending detection
# ---------------------------------------------------------------------------

def read_json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def write_annotated(path: Path, annotated: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(annotated, indent=2, ensure_ascii=False) + "\n",
                    "utf-8")


def is_pending(verified_path: Path, annotated_path: Path) -> bool:
    """A tune needs (re-)annotation when its annotated file is missing, was
    built from a different source (hash mismatch — this also demotes stale
    human verifications, spec §3.5), or recorded an LLM failure and has not
    been human-verified since.
    """
    if not annotated_path.exists():
        return True
    try:
        annotation = read_json(annotated_path)["key_annotation"]
    except (json.JSONDecodeError, KeyError):
        return True
    if annotation.get("source_sha256") != source_sha256(verified_path):
        return True
    return (annotation.get("status") != STATUS_VERIFIED
            and "error" in (annotation.get("llm") or {}))
