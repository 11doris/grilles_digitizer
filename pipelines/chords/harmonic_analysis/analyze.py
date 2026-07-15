"""Functional harmony analyzer (harmonic_analysis_spec §1/§2).

Computes, per part of a strains-model tune (legacy `sections` maps work too
via `sections_view`):

* `chords`  — a roman numeral for every printed chord, relative to its key
              context (global key, `section_keys` entry, or a detected local
              region), with device roles (secondary dominant, tritone sub,
              related ii, backdoor, passing diminished) expressed as the
              book's slash numerals (V7/V, subV7/IV, ii7/V, subii7/IV);
* `links`   — the arrows & brackets lane: solid/dotted ii–V brackets,
              solid (down a fifth) / dotted (half step) resolution arrows
              from dominants, headless to-minor arrows;
* `regions` — local key stretches: `section_keys` verbatim (kind "section")
              plus detected tonicizations of >= REGION_MIN_BARS bars, with a
              confidence and a pivot reading at the seam;
* `blocks`  — named building blocks matched from catalog.json plus the
              code-detected ii–V chains and dominant cycles.

Everything is a pure function of (chords, key, section_keys): a key
correction simply recomputes the analysis (spec §1 — no staleness).
Numerals keep ASCII accidentals ("bIII", "7b9"); renderers map them to
glyphs. `Δ` and `ø` are written directly (the files are UTF-8).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pipelines.chords.similarity.normalize import (
    PC_NAME, Chord, ChordParseError, iter_parts, parse_chord, part_ids,
    pitch_class, resolve_part_ref, sections_view,
)
from pipelines.chords.similarity.normalize import degree_name as _degree_name

ANALYSIS_VERSION = 1

# A resolved tonicization shorter than this stays slash-notated in the
# prevailing key; from this many bars on it opens its own key region (§2.3).
REGION_MIN_BARS = 4
# The relative major/minor of the prevailing key needs a longer stretch —
# short excursions to the relative key are idiomatic, not a new key.
RELATIVE_MIN_BARS = 8
# Confidence = the strictly-diatonic share of a candidate region's chords.
# Candidates below MIN_REGION_CONFIDENCE are not applied at all (a dominant
# cycle "works toward" many keys without being in any of them); applied
# regions below FLAG_CONFIDENCE are flagged for a spot check (owner
# decision: no verification UI, just the report).
MIN_REGION_CONFIDENCE = 0.7
FLAG_CONFIDENCE = 0.8

_UPPER_DEGREE = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV",
                 6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
# Sharp spellings for ascending passing diminished chords (♯Io7, ♯IIo7, …).
_SHARP_DEGREE = {1: "#I", 3: "#II", 6: "#IV", 8: "#V", 10: "#VI"}

# Diatonic root pitch classes per mode (minor blends natural + harmonic).
_DIATONIC_PCS = {"major": frozenset({0, 2, 4, 5, 7, 9, 11}),
                 "minor": frozenset({0, 2, 3, 5, 7, 8, 10, 11})}
# Chord qualities that read as *the* diatonic chord on a degree (region
# confidence counts these; the V degree accepts both V7 and a plain triad).
_DIATONIC_QUALITY = {
    "major": {0: {"maj", "sus"}, 2: {"min", "sus"}, 4: {"min"}, 5: {"maj"},
              7: {"dom", "maj", "sus"}, 9: {"min"}, 11: {"m7b5", "dim"}},
    "minor": {0: {"min"}, 2: {"m7b5", "min"}, 3: {"maj"}, 5: {"min", "dom"},
              7: {"dom", "min", "maj"}, 8: {"maj"}, 10: {"dom", "maj"},
              11: {"dim"}},
}


@dataclass(frozen=True)
class Ctx:
    """A key context numerals are computed against."""
    tonic_pc: int
    mode: str  # "major" | "minor"

    @property
    def tonic(self) -> str:
        return PC_NAME[self.tonic_pc]

    def same(self, other: "Ctx") -> bool:
        return self.tonic_pc == other.tonic_pc and self.mode == other.mode

    def is_relative_of(self, other: "Ctx") -> bool:
        """Relative major/minor pair (shared signature, different tonic)."""
        if self.mode == other.mode:
            return False
        major, minor = (self, other) if self.mode == "major" else (other, self)
        return (minor.tonic_pc + 3) % 12 == major.tonic_pc


@dataclass(frozen=True)
class Event:
    """One printed chord: its position and parsed symbol."""
    idx: int   # index among the part's sounding events
    bar: int   # stored (1-based) bar number within the part
    beat: int
    chord: Chord

    @property
    def pos(self) -> list:
        return [self.bar, self.beat]


def _ctx_of(key: dict) -> Ctx:
    return Ctx(pitch_class(key["tonic"]),
               "minor" if key.get("mode") == "minor" else "major")


def _part_events(bars: list[dict]) -> list[Event]:
    """The part's printed chords in playing order (N.C. slots dropped —
    there is nothing to name and devices skip over them)."""
    events: list[Event] = []
    for i, bar in enumerate(bars):
        barno = bar.get("bar", i + 1)
        beats = sorted((int(k), v) for k, v in (bar.get("beats") or {}).items())
        for beat, symbol in beats:
            chord = parse_chord(symbol)
            if chord.is_sounding:
                events.append(Event(len(events), barno, beat, chord))
    return events


# ---------------------------------------------------------------------------
# Numeral text
# ---------------------------------------------------------------------------

def _suffix(chord: Chord) -> str:
    """The quality text appended to a numeral. The leading "m" of a minor
    stem is dropped (the lowercase numeral already says minor), m7b5 reads
    ø7, maj7/maj9 read Δ/Δ9 (the book's triangle); alterations stay
    verbatim. Slash basses and the uncertainty marker never enter numerals.
    """
    ext = chord.extensions
    ext = re.sub(r"/(?:[A-G](?:#|b)?|[2-7])$", "", ext.rstrip("?")).rstrip("?")
    if ext.startswith("m7b5"):
        return "ø7" + ext[len("m7b5"):]
    if ext.startswith("m(maj7)"):
        return "Δ7" + ext[len("m(maj7)"):]
    if chord.quality == "min":
        ext = ext[1:]  # the stem always starts with its "m"
    ext = ext.replace("maj7", "Δ").replace("maj9", "Δ9")
    return ext


def _numeral(chord: Chord, ctx: Ctx) -> str:
    return _degree_name(chord.root_pc, ctx.tonic_pc, chord.quality) + _suffix(chord)


def _slash_target(target_pc: int, ctx: Ctx) -> str:
    """The book writes the slash target as an uppercase degree (V7/VI,
    subV7/IV) whatever chord actually sits there."""
    return _UPPER_DEGREE[(target_pc - ctx.tonic_pc) % 12]


# ---------------------------------------------------------------------------
# Diatonic / functional membership (region detection, pivots)
# ---------------------------------------------------------------------------

def _is_diatonic(chord: Chord, ctx: Ctx) -> bool:
    allowed = _DIATONIC_QUALITY[ctx.mode].get((chord.root_pc - ctx.tonic_pc) % 12)
    return allowed is not None and chord.quality in allowed


def _is_functional(chord: Chord, ctx: Ctx) -> bool:
    """Diatonic, or a device pointing at a diatonic degree: a secondary
    dominant / subV, their related ii, or any diminished passing chord."""
    if _is_diatonic(chord, ctx):
        return True
    pcs = _DIATONIC_PCS[ctx.mode]
    rel = (chord.root_pc - ctx.tonic_pc) % 12
    if chord.quality == "dom":
        return (rel - 7) % 12 in pcs or (rel - 1) % 12 in pcs
    if chord.quality in ("min", "m7b5"):
        return (rel - 2) % 12 in pcs  # the ii of some diatonic degree
    return chord.quality == "dim"


# ---------------------------------------------------------------------------
# Devices: resolutions, brackets, roles
# ---------------------------------------------------------------------------

def _resolutions(events: list[Event], next_event: Event | None
                 ) -> dict[int, tuple[str, Event, bool]]:
    """{event idx: (kind, target, external)} for every dominant that
    resolves into the next sounding chord: "fifth" (down a perfect fifth),
    "half" (down a half step, the subV move) or "backdoor" (bVII7 up a whole
    step). The part-final chord may resolve into `next_event` — the first
    chord of the following part (the form continues); such a resolution is
    marked external and names the dominant without drawing an arrow."""
    out: dict[int, tuple[str, Event, bool]] = {}
    pairs = list(zip(events, events[1:], [False] * (len(events) - 1)))
    if next_event is not None and events:
        pairs.append((events[-1], next_event, True))
    for e, nxt, external in pairs:
        if e.chord.quality != "dom":
            continue
        rel = (e.chord.root_pc - nxt.chord.root_pc) % 12
        kind = {7: "fifth", 1: "half", 10: "backdoor"}.get(rel)
        if kind:
            out[e.idx] = (kind, nxt, external)
    return out


def _analyze_part(events: list[Event], part_ctx: Ctx, part_id: str,
                  catalog: list[dict], flags: list[str],
                  next_event: Event | None = None) -> dict:
    regions = _detect_regions(events, part_ctx, part_id, flags)

    def ctx_at(idx: int) -> Ctx:
        for reg in regions:
            if reg["_first"] <= idx <= reg["_last"]:
                return reg["_ctx"]
        return part_ctx

    resolutions = _resolutions(events, next_event)

    # Numerals + roles. Dominants that resolve get the book's slash names
    # (relative to their own context); their related ii is renamed to match.
    numerals: dict[int, str] = {}
    roles: dict[int, str] = {}
    for e in events:
        ctx = ctx_at(e.idx)
        numerals[e.idx] = _numeral(e.chord, ctx)
        res = resolutions.get(e.idx)
        if res:
            kind, target, _external = res
            t_rel = (target.chord.root_pc - ctx.tonic_pc) % 12
            if kind == "fifth" and t_rel != 0 and t_rel in _DIATONIC_PCS[ctx.mode]:
                numerals[e.idx] = ("V7/"
                                   + _slash_target(target.chord.root_pc, ctx))
                roles[e.idx] = "sec_dom"
            elif kind == "half":
                roles[e.idx] = "sub_v"
                numerals[e.idx] = "subV7" if t_rel == 0 else (
                    "subV7/" + _slash_target(target.chord.root_pc, ctx))
            elif kind == "backdoor" and t_rel == 0:
                roles[e.idx] = "backdoor"
        elif e.chord.quality == "dim":
            nxt = events[e.idx + 1] if e.idx + 1 < len(events) else None
            if nxt:
                step = (nxt.chord.root_pc - e.chord.root_pc) % 12
                if step == 1:
                    # Ascending passing dim is spelled sharp in the book
                    # (C♯o7 between C and Dm7 reads ♯Io7, not ♭IIo7).
                    roles[e.idx] = "dim_passing"
                    rel = (e.chord.root_pc - ctx.tonic_pc) % 12
                    if rel in _SHARP_DEGREE:
                        numerals[e.idx] = (_SHARP_DEGREE[rel].lower()
                                           + _suffix(e.chord))
                elif step == 11:
                    roles[e.idx] = "dim_passing"
                elif step == 0:
                    roles[e.idx] = "dim_aux"

    # Brackets + related-ii renames. Shape A: ii a fifth above its dominant
    # (solid; dotted when the dominant is a subV — the pair is subii/subV).
    # Shape B: ii a half step above a subV (Dm7 Db7 -> C, dotted).
    links: list[dict] = []
    for p, d in zip(events, events[1:]):
        if p.chord.quality not in ("min", "m7b5") or d.chord.quality != "dom":
            continue
        shape = (p.chord.root_pc - d.chord.root_pc) % 12
        if shape not in (7, 1):
            continue
        kind_target = resolutions.get(d.idx)
        d_name = numerals[d.idx]
        two = "iiø7" if p.chord.quality == "m7b5" else "ii7"
        if shape == 7:
            dotted = bool(kind_target) and kind_target[0] == "half"
            links.append({"type": "iiV_sub" if dotted else "iiV",
                          "from": p.pos, "to": d.pos})
            if "/" in d_name:  # V7/x or subV7/x — the ii inherits the target
                prefix = "sub" + two if d_name.startswith("subV7/") else two
                numerals[p.idx] = prefix + "/" + d_name.split("/", 1)[1]
                roles[p.idx] = "sec_ii"
            elif d_name == "subV7":
                numerals[p.idx] = "sub" + two
                roles[p.idx] = "sec_ii"
        elif kind_target and kind_target[0] == "half":
            links.append({"type": "iiV_sub", "from": p.pos, "to": d.pos})
            if "/" in d_name:  # the true ii of the subV's target
                numerals[p.idx] = two + "/" + d_name.split("/", 1)[1]
                roles[p.idx] = "sec_ii"

    # Resolution arrows (from every resolving dominant) and to-minor arrows.
    for e, nxt in zip(events, events[1:]):
        res = resolutions.get(e.idx)
        if res and res[0] in ("fifth", "half"):
            links.append({"type": res[0], "from": e.pos, "to": res[1].pos})
        if (e.chord.quality in ("maj", "dom")
                and nxt.chord.root_pc == e.chord.root_pc
                and nxt.chord.quality in ("min", "m7b5")):
            links.append({"type": "to_minor", "from": e.pos, "to": nxt.pos})

    chords = []
    for e in events:
        entry: dict = {"bar": e.bar, "beat": e.beat, "numeral": numerals[e.idx]}
        if e.idx in roles:
            entry["role"] = roles[e.idx]
        for reg in regions:
            if reg["_first"] == e.idx and reg.get("_pivot"):
                entry["pivot"] = reg["_pivot"]
        chords.append(entry)

    blocks = _match_blocks(events, numerals, ctx_at, catalog, links)

    out: dict = {"chords": chords}
    if links:
        order = {"iiV": 0, "iiV_sub": 0, "fifth": 1, "half": 1, "backdoor": 1,
                 "to_minor": 2}
        links.sort(key=lambda l: (l["from"], order.get(l["type"], 3)))
        out["links"] = links
    if regions:
        out["regions"] = [{k: v for k, v in reg.items()
                           if not k.startswith("_")} for reg in regions]
    if blocks:
        out["blocks"] = blocks
    return out


# ---------------------------------------------------------------------------
# Key regions (§2.3)
# ---------------------------------------------------------------------------

def _detect_regions(events: list[Event], part_ctx: Ctx, part_id: str,
                    flags: list[str]) -> list[dict]:
    """Tonicization regions inside one part: each resolved dominant seeds a
    candidate key at its target; the candidate grows backward over chords
    functional in it and forward over diatonic/functional ones. Candidates
    of >= REGION_MIN_BARS bars (RELATIVE_MIN_BARS for the relative key)
    survive; overlaps resolve longest-first."""
    candidates: list[dict] = []
    for i, (e, target) in enumerate(zip(events, events[1:])):
        if e.chord.quality != "dom":
            continue
        if (e.chord.root_pc - target.chord.root_pc) % 12 != 7:
            continue
        mode = ("minor" if target.chord.quality in ("min", "m7b5")
                else "major")
        ctx = Ctx(target.chord.root_pc, mode)
        if ctx.same(part_ctx):
            continue
        # Backward: chords that work toward the candidate key AND have left
        # the outer key (an outer-diatonic chord ends the growth — otherwise
        # a ii–V to IV would swallow the whole part, since everything in the
        # outer key is "functional" somewhere in a nearby key).
        first = i
        while (first > 0
               and _is_functional(events[first - 1].chord, ctx)
               and not _is_diatonic(events[first - 1].chord, part_ctx)):
            first -= 1
        core_first = first  # span is measured on the core (pivot excluded)
        # One more step for a pivot chord — diatonic in BOTH keys, it opens
        # the region with the stacked dual reading (§2.3).
        if (first > 0 and _is_diatonic(events[first - 1].chord, ctx)
                and _is_diatonic(events[first - 1].chord, part_ctx)):
            first -= 1
        # Forward: chords that have no reading in the outer key, plus each
        # direct re-cadence onto the candidate tonic. A chord the outer key
        # explains (Dm7 after the region's last F, say) ends the region —
        # the ear is back home.
        last = i + 1
        while last + 1 < len(events):
            cur, nxt = events[last], events[last + 1]
            recadence = (cur.chord.quality == "dom"
                         and (cur.chord.root_pc - nxt.chord.root_pc) % 12 == 7
                         and nxt.chord.root_pc == ctx.tonic_pc)
            if recadence or (_is_functional(nxt.chord, ctx)
                             and not _is_diatonic(nxt.chord, part_ctx)):
                last += 1
            else:
                break
        span = events[last].bar - events[core_first].bar + 1
        min_bars = (RELATIVE_MIN_BARS if ctx.is_relative_of(part_ctx)
                    else REGION_MIN_BARS)
        if span < min_bars:
            continue
        inside = events[first:last + 1]
        conf = (sum(_is_diatonic(ev.chord, ctx) for ev in inside)
                / len(inside))
        if conf < MIN_REGION_CONFIDENCE:
            continue
        candidates.append({"_first": first, "_last": last, "_ctx": ctx,
                           "span": span, "conf": conf})

    candidates.sort(key=lambda c: (-(c["_last"] - c["_first"]), -c["conf"]))
    chosen: list[dict] = []
    for cand in candidates:
        if any(not (cand["_last"] < r["_first"] or r["_last"] < cand["_first"])
               for r in chosen):
            continue
        chosen.append(cand)
    chosen.sort(key=lambda c: c["_first"])

    regions: list[dict] = []
    for cand in chosen:
        ctx: Ctx = cand["_ctx"]
        first, last = cand["_first"], cand["_last"]
        reg = {
            "from": events[first].pos, "to": events[last].pos,
            "tonic": ctx.tonic, "mode": ctx.mode,
            # A stretch that runs to the part's end reads as a modulation
            # (the part leaves in the new key); anything else returns.
            "kind": ("modulation" if last == len(events) - 1
                     and first > 0 else "tonicization"),
            "confidence": round(cand["conf"], 2),
            "_first": first, "_last": last, "_ctx": ctx,
        }
        # Pivot reading (§2.3): the seam chord seen from the outer key.
        seam = events[first].chord
        if first > 0 and _is_diatonic(seam, part_ctx):
            reg["_pivot"] = {"key": part_ctx.tonic, "mode": part_ctx.mode,
                             "numeral": _numeral(seam, part_ctx)}
        if cand["conf"] < FLAG_CONFIDENCE:
            flags.append(
                f"part {part_id}: low-confidence {reg['kind']} to "
                f"{ctx.tonic} {ctx.mode} (confidence {reg['confidence']})")
        if first == 0 and last == len(events) - 1:
            flags.append(
                f"part {part_id}: reads entirely in {ctx.tonic} {ctx.mode} "
                "— candidate section key")
        regions.append(reg)
    return regions


# ---------------------------------------------------------------------------
# Building blocks (§2.4/§5)
# ---------------------------------------------------------------------------

_CATALOG_PATH = Path(__file__).with_name("catalog.json")
_ROMAN_PC = {"I": 0, "II": 2, "III": 4, "IV": 5, "V": 7, "VI": 9, "VII": 11}


def load_catalog(path: Path = _CATALOG_PATH) -> list[dict]:
    """Catalog entries with their patterns parsed to (pc, quality) tokens."""
    entries = json.loads(path.read_text("utf-8"))
    for entry in entries:
        entry["_tokens"] = [_parse_pattern_token(t)
                            for t in entry["pattern"].split()]
    return entries


def _parse_pattern_token(token: str) -> tuple[int, str]:
    """"bII:dim" -> (1, "dim"); quality "any" matches every class."""
    deg, _, quality = token.partition(":")
    m = re.match(r"^([b#]*)([ivIV]+)$", deg)
    if not m or m.group(2).upper() not in _ROMAN_PC:
        raise ValueError(f"bad catalog degree {deg!r} in {token!r}")
    pc = _ROMAN_PC[m.group(2).upper()]
    for acc in m.group(1):
        pc += 1 if acc == "#" else -1
    return pc % 12, quality or "any"


def _match_blocks(events: list[Event], numerals: dict[int, str], ctx_at,
                  catalog: list[dict], links: list[dict]) -> list[dict]:
    """Catalog patterns matched over consecutive chords sharing one key
    context, plus the code-detected ii–V chains, dominant cycles and
    root-motion runs. Overlaps (§2.4): named blocks first (longest span
    wins, earlier catalog entry breaks ties), then the runs clipped into
    the gaps, then the generic plain cadences into whatever space is left.
    """
    candidates: list[tuple[int, int, int, dict]] = []  # (first,last,prio,block)
    generic: list[tuple[int, int, int, dict]] = []

    for prio, entry in enumerate(catalog):
        tokens = entry["_tokens"]
        max_bars = entry.get("max_bars", 8)
        for start in range(len(events) - len(tokens) + 1):
            window = events[start:start + len(tokens)]
            ctx = ctx_at(window[0].idx)
            if any(not ctx_at(e.idx).same(ctx) for e in window[1:]):
                continue
            if window[-1].bar - window[0].bar + 1 > max_bars:
                continue
            ok = all((e.chord.root_pc - ctx.tonic_pc) % 12 == pc
                     and (quality == "any" or e.chord.quality == quality)
                     for e, (pc, quality) in zip(window, tokens))
            if ok:
                pool = generic if entry.get("generic") else candidates
                pool.append((start, start + len(tokens) - 1, prio, {
                    "id": entry["id"], "name": entry["name"],
                    "from": window[0].pos, "to": window[-1].pos}))

    n_catalog = len(catalog)
    # ii–V chains: >= 2 back-to-back bracket pairs (the second ii follows
    # the first V immediately).
    pairs = sorted((_pos_index(events, l["from"]), _pos_index(events, l["to"]))
                   for l in links if l["type"] in ("iiV", "iiV_sub"))
    run: list[tuple[int, int]] = []
    for pair in pairs + [(len(events) + 9, 0)]:  # sentinel flushes the run
        if run and pair[0] == run[-1][1] + 1:
            run.append(pair)
            continue
        if len(run) >= 2:
            candidates.append((run[0][0], run[-1][1], n_catalog, {
                "id": "iiv_chain", "name": "ii–V chain",
                "from": events[run[0][0]].pos, "to": events[run[-1][1]].pos}))
        run = [pair]
    # Dominant cycles: >= 3 dominants each resolving down a fifth.
    cycle_start = None
    for i, e in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        chains = (nxt is not None and e.chord.quality == "dom"
                  and (e.chord.root_pc - nxt.chord.root_pc) % 12 == 7)
        if chains and cycle_start is None:
            cycle_start = i
        elif not chains and cycle_start is not None:
            # i is the landing chord of the last arrow when it's not itself
            # a chaining dominant; the cycle covers start..i.
            if i - cycle_start >= 3:
                candidates.append((cycle_start, i, n_catalog + 1, {
                    "id": "dominant_cycle", "name": "Dominant cycle",
                    "from": events[cycle_start].pos, "to": events[i].pos}))
            cycle_start = None

    def fits(first: int, last: int) -> bool:
        return all(last < c[0] or c[1] < first for c in chosen)

    # Named blocks: longest span wins, earlier catalog entry breaks ties.
    candidates.sort(key=lambda c: (-(c[1] - c[0]), c[2], c[0]))
    chosen: list[tuple[int, int, int, dict]] = []
    for cand in candidates:
        if fits(cand[0], cand[1]):
            chosen.append(cand)

    # Root-motion runs (chromatic descent, circle of fifths) are clipped
    # into the gaps — a turnaround, chain or cycle keeps the book's name
    # even when a longer run of falling roots passes through it — and
    # survive when enough distinct roots remain.
    for first, last, min_roots, block_id, name in _root_runs(events):
        first, last = _clip(first, last, chosen)
        if (first <= last and
                len({e.chord.root_pc for e in events[first:last + 1]})
                >= min_roots):
            chosen.append((first, last, 0, {
                "id": block_id, "name": name,
                "from": events[first].pos, "to": events[last].pos}))

    # The generic plain cadences take whatever space is left: inside a
    # detected run the run is the better name for the same chords.
    generic.sort(key=lambda c: (-(c[1] - c[0]), c[2], c[0]))
    for cand in generic:
        if fits(cand[0], cand[1]):
            chosen.append(cand)

    chosen.sort(key=lambda c: c[0])
    return [c[3] for c in chosen]


# Root-motion run kinds (§2.4): (min distinct roots, id, name, step rule).
# The step rule maps (semitones down to the next root, non-primary steps
# already taken) -> (allowed, counts as non-primary). The circle of fifths
# allows one diminished-fifth step for the diatonic circle's IV–vii seam.
_RUN_KINDS = (
    (4, "chromatic_descent", "Chromatic descent",
     lambda rel, used: (rel == 1, 0)),
    (5, "circle_of_fifths", "Circle of fifths",
     lambda rel, used: (rel == 7 or (rel == 6 and used == 0),
                        1 if rel == 6 else 0)),
)


def _root_runs(events: list[Event]):
    """Maximal root-motion stretches, per _RUN_KINDS kind. Same-root
    reprints stay inside a run; a run of >= the kind's distinct-root minimum
    yields (first, last, min_roots, id, name) trimmed to the motion — the
    leading root's last print through the landing root's first print."""
    for min_roots, block_id, name, step_ok in _RUN_KINDS:
        i = 0
        while i < len(events):
            j, roots, used = i, 1, 0
            while j + 1 < len(events):
                rel = (events[j].chord.root_pc
                       - events[j + 1].chord.root_pc) % 12
                if rel == 0:
                    j += 1
                    continue
                ok, secondary = step_ok(rel, used)
                if not ok:
                    break
                used += secondary
                roots += 1
                j += 1
            if roots >= min_roots:
                first, last = i, j
                while events[first + 1].chord.root_pc == events[first].chord.root_pc:
                    first += 1
                while events[last - 1].chord.root_pc == events[last].chord.root_pc:
                    last -= 1
                yield first, last, min_roots, block_id, name
            i = j + 1


def _clip(first: int, last: int, chosen: list[tuple]) -> tuple[int, int]:
    """Clip the run [first, last] out of the chosen blocks' spans. A block
    strictly inside the run empties it — runs are never split in two."""
    spans = sorted((c[0], c[1]) for c in chosen)
    changed = True
    while changed and first <= last:
        changed = False
        for ci, cj in spans:
            if cj < first or ci > last:
                continue
            if ci <= first:
                first, changed = cj + 1, True
            elif cj >= last:
                last, changed = ci - 1, True
            else:
                return 1, 0  # a chosen block splits the run: drop it
    return first, last


def _pos_index(events: list[Event], pos: list) -> int:
    for e in events:
        if e.pos == pos:
            return e.idx
    raise ValueError(f"no event at {pos}")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _local_keys_by_part(tune: dict, section_keys: dict | None) -> dict:
    """Resolve section_keys references (part ids or bare strain names of
    single-part strains) to generated part ids; legacy section keys pass
    through unchanged."""
    out: dict = {}
    for ref, key in (section_keys or {}).items():
        hit = resolve_part_ref(tune, ref)
        pid = part_ids(hit[0])[hit[1]] if hit else ref
        out[pid] = key
    return out


def analyze_tune(tune: dict, key: dict, section_keys: dict | None = None,
                 catalog: list[dict] | None = None) -> dict:
    """The `harmonic_analysis` document for one tune (spec §3).

    A part whose chords fail to parse is skipped with a flag instead of
    failing the tune — verified files pass the syntax checker, so this is
    a safety net, not a code path.
    """
    if catalog is None:
        catalog = load_catalog()
    global_ctx = _ctx_of(key)
    local_keys = _local_keys_by_part(tune, section_keys)

    flags: list[str] = []
    events_by: dict[str, list[Event]] = {}
    for pid, bars in sections_view(tune).items():
        try:
            events_by[pid] = _part_events(bars)
        except ChordParseError as exc:
            flags.append(f"part {pid}: {exc}")

    # A part-final dominant may resolve into the next part (the form keeps
    # playing): its continuation is the following part's first chord, and
    # the last part wraps around to the first part of its own strain.
    strain_of = {pid: strain.get("name")
                 for pid, strain, _part in iter_parts(tune)}
    order = list(events_by)

    def continuation(k: int) -> Event | None:
        if k + 1 < len(order):
            nxt = events_by[order[k + 1]]
        else:
            mine = strain_of.get(order[k])
            home = next((p for p in order if strain_of.get(p) == mine),
                        order[0])
            nxt = events_by[home]
        return nxt[0] if nxt else None

    parts: dict[str, dict] = {}
    for k, pid in enumerate(order):
        events = events_by[pid]
        if not events:
            continue
        part_ctx = (_ctx_of(local_keys[pid]) if pid in local_keys
                    else global_ctx)
        analysis = _analyze_part(events, part_ctx, pid, catalog, flags,
                                 next_event=continuation(k))
        # Section-keyed parts read entirely in their own key: record it as
        # a kind "section" region over the whole part, ahead of any
        # detected sub-regions, so renderers have one source for prefixes.
        if pid in local_keys:
            analysis.setdefault("regions", []).insert(0, {
                "from": events[0].pos, "to": events[-1].pos,
                "tonic": PC_NAME[part_ctx.tonic_pc], "mode": part_ctx.mode,
                "kind": "section", "confidence": 1.0,
            })
        parts[pid] = analysis

    out: dict = {"version": ANALYSIS_VERSION, "parts": parts}
    if flags:
        out["flags"] = flags
    return out


def analyze_annotated(annotated: dict) -> dict:
    """Analysis for a 05_annotated document (its resolved key + section
    keys). The caller assigns the result to `harmonic_analysis`."""
    return analyze_tune(annotated, annotated["key"],
                        annotated.get("section_keys"))
