"""Chord parsing, quality reduction and grid expansion (tune_similarity_spec §4).

Pure functions, no I/O. The chord grammar mirrors
pipelines/chords/tools/check_chord_syntax.py — that file is the authoritative
vocabulary; this module only *interprets* symbols the checker accepts.
Parsing failure raises ChordParseError so pipelines can fail loudly (spec §10:
never a silent skip).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Pitch classes
# ---------------------------------------------------------------------------

_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# pc -> spelling in this book's vocabulary (spec §3.1: F, Bb, Eb, Db, F#, ...)
PC_NAME = {0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F",
           6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}


def pitch_class(name: str) -> int:
    """'Bb' -> 10, 'F#' -> 6. Raises KeyError on garbage."""
    pc = _LETTER_PC[name[0]]
    for acc in name[1:]:
        pc += {"#": 1, "b": -1}[acc]
    return pc % 12


# ---------------------------------------------------------------------------
# Chord parser
# ---------------------------------------------------------------------------

class ChordParseError(ValueError):
    pass


# Quality classes (spec §4.1): maj, min, dom, m7b5, dim, aug, sus.
# "nc" is the internal class for N.C. slots.

_ROOT = r"[A-G](?:#|b)?"
_ALT = r"(?:b5|#5|b9|#9|#11|b13)"
_STEMS = [
    "", "m", "6", "7", "9", "11", "13", "69",
    "maj7", "maj9",
    "m6", "m7", "m9", "m11", "m13", "m69", "m7b5",
    "o7", "m(maj7)",
    "sus4", "sus2", "7sus4", "9sus4",
]
_CORE = re.compile(
    rf"^(?P<root>{_ROOT})"
    rf"(?P<stem>{'|'.join(sorted(map(re.escape, _STEMS), key=len, reverse=True))})"
    rf"(?P<pext>\((?:6|7|9|11|13)\))?"
    rf"(?P<altw>alt)?"
    rf"(?P<balts>{_ALT}*)"
    rf"(?P<palts>\({_ALT}+\))?"
    rf"(?P<slash>/(?:{_ROOT}|[2-7]))?"  # bass note (F/Bb) or degree in the bass (F/5)
    rf"(?P<unc>\?)?$"
)

_STEM_CLASS = {
    "": "maj", "6": "maj", "maj7": "maj", "maj9": "maj", "69": "maj",
    "m": "min", "m6": "min", "m7": "min", "m9": "min", "m11": "min",
    "m13": "min", "m69": "min", "m(maj7)": "min",
    "7": "dom", "9": "dom", "11": "dom", "13": "dom",
    "m7b5": "m7b5",
    "o7": "dim",
    "sus4": "sus", "sus2": "sus", "7sus4": "sus", "9sus4": "sus",
}


@dataclass(frozen=True)
class Chord:
    symbol: str            # original printed symbol, parens/uncertainty intact
    root_pc: int | None    # None only for N.C.
    quality: str           # maj | min | dom | m7b5 | dim | aug | sus | nc
    extensions: str        # everything after root+stem, informational
    bass_pc: int | None    # slash bass, if any
    parenthesized: bool    # printed as an optional chord, e.g. (F)
    uncertain: bool        # printed with a trailing '?'

    @property
    def is_sounding(self) -> bool:
        return self.root_pc is not None


def parse_chord(symbol: str) -> Chord:
    """Parse one printed chord symbol into (root pc, quality class, ...)."""
    if symbol == "N.C.":
        return Chord(symbol, None, "nc", "", None, False, False)

    core, parenthesized = symbol, False
    if symbol.startswith("(") and symbol.endswith(")"):
        inner = symbol[1:-1]
        depth = 0
        for c in inner:
            depth += c == "("
            depth -= c == ")"
            if depth < 0:  # not one wrapping pair, e.g. would be malformed
                break
        else:
            core, parenthesized = inner, True

    m = _CORE.match(core)
    if not m:
        raise ChordParseError(f"unparseable chord symbol: {symbol!r}")

    stem = m.group("stem")
    quality = _STEM_CLASS[stem]
    alts = (m.group("balts") or "") + (m.group("palts") or "").strip("()")

    # Alteration-driven reclassification of bare triads:
    #   F(#5)/F+ style  -> aug; F(b9), D(b9), A(#9#5) -> implied dominant.
    if quality == "maj" and stem == "" and not m.group("pext"):
        if re.search(r"b9|#9|#11|b13", alts):
            quality = "dom"
        elif "#5" in alts:
            quality = "aug"
    # A parenthesised extension on a bare triad, e.g. F(13) -> dominant colour;
    # F(6) stays major.
    if quality == "maj" and stem == "" and m.group("pext") and m.group("pext") != "(6)":
        quality = "dom"
    # 'alt' always implies a dominant.
    if m.group("altw"):
        quality = "dom"

    bass = m.group("slash")
    # A numeric slash bass (F/5 = the fifth in the bass) records a scale DEGREE,
    # not an absolute note, so it has no pitch class — the degree stays in the
    # `symbol`/`extensions`. A note bass (F/Bb) resolves to a pitch class as before.
    note_bass = bass is not None and not bass[1].isdigit()
    return Chord(
        symbol=symbol,
        root_pc=pitch_class(m.group("root")),
        quality=quality,
        extensions=core[len(m.group("root")):],
        bass_pc=pitch_class(bass[1:]) if note_bass else None,
        parenthesized=parenthesized,
        uncertain=bool(m.group("unc")),
    )


# ---------------------------------------------------------------------------
# Strain model read layer (strain_model_phase_c_plan §3/§4/§7)
# ---------------------------------------------------------------------------
#
# A Phase C tune stores an ordered `strains` list instead of the legacy
# `sections` map. Each strain has an explicit `name` and `role`, and ordered
# `parts` each carrying a printed `label` (primes kept), an optional `plays`
# repeat count and its `bars` (the chord payload, unchanged). Nothing here is
# ever parsed back out of a key string.
#
# Per-part addressing:
#   * structured anchors (`variants[].targets[]`, `coda_jump.from`) use the
#     object form {"strain": name, "part": 0-based index, "bar": 1-based};
#   * map-keyed fields (`section_keys`, similarity output) use the generated
#     part id — chorus parts read as classic letters ("A", "A1", "B"), other
#     strains prefix their name ("verse_A", "impro_B"), a single-part aux
#     connector is just its name ("coda"). Ids are GENERATED from the
#     structure (labels), never parsed; `resolve_part_ref` maps one back.

ROLES = ("chorus", "verse", "strain", "aux")

# Auxiliary connector vocabulary (case-insensitive on input, stored lowercase).
# A connector name outside this set is a loud validation error, so a
# capitalised or misspelled connector can never silently misgroup.
AUX_CONNECTORS = frozenset({"intro", "coda", "interlude", "transition",
                            "tag", "vamp"})


def _label_base(label) -> str:
    """A part id fragment from a printed label: primes and whitespace
    dropped ("A'" -> "A", "BLUES" -> "BLUES")."""
    s = re.sub(r"['’]+$", "", str(label or "").strip())
    s = re.sub(r"\s+", "", s)
    return s or "P"


def part_ids(strain: dict) -> list[str]:
    """Generated ids of a strain's parts, in order (see module comment)."""
    parts = strain.get("parts") or []
    name, role = strain.get("name"), strain.get("role")
    if role == "aux" and len(parts) == 1:
        return [str(name)]
    ids: list[str] = []
    counts: dict[str, int] = {}
    for part in parts:
        base = _label_base(part.get("label"))
        n = counts.get(base, 0)
        counts[base] = n + 1
        suffix = base if n == 0 else f"{base}{n}"
        ids.append(suffix if role == "chorus" else f"{name}_{suffix}")
    return ids


def iter_parts(tune: dict):
    """Yield (part_id, strain, part) over a strains-model tune, in document
    (= printed / played) order."""
    for strain in tune.get("strains") or []:
        for pid, part in zip(part_ids(strain), strain.get("parts") or []):
            yield pid, strain, part


def sections_view(tune: dict) -> dict:
    """The tune's playable units as an ordered {part_id: bars} map.

    For a strains-model tune the ids are generated (`part_ids`); a legacy
    tune (raw digitizer output, still a `sections` map) passes through
    unchanged. Every expansion consumer (similarity, scorer, LLM payload)
    reads this view, so both shapes stay expandable.
    """
    if "strains" in tune:
        return {pid: part.get("bars") or []
                for pid, _strain, part in iter_parts(tune)}
    return tune.get("sections") or {}


def part_roles(tune: dict) -> dict[str, str]:
    """{part_id: role} for a strains-model tune; {} for a legacy tune."""
    return {pid: strain.get("role")
            for pid, strain, _part in iter_parts(tune)}


def strain_label_seq(strain: dict) -> list[str]:
    """The strain's printed label sequence, repeats expanded: a part with
    `plays: N` contributes its label N times (the old form_strains labels)."""
    out: list[str] = []
    for part in strain.get("parts") or []:
        out.extend([part.get("label")] * int(part.get("plays") or 1))
    return out


def strain_bars_total(strain: dict) -> int:
    """Total printed bars of a strain: stored bars times plays, summed."""
    return sum(len(part.get("bars") or []) * int(part.get("plays") or 1)
               for part in strain.get("parts") or [])


def derived_form_strains(tune: dict) -> dict:
    """The legacy `form_strains` shape ({name: {bars, labels}}), computed on
    the fly from `strains` — derived, never stored (Phase C §5)."""
    return {s["name"]: {"bars": strain_bars_total(s),
                        "labels": strain_label_seq(s)}
            for s in tune.get("strains") or []}


def is_compared(strain: dict) -> bool:
    """Whether a strain's parts enter similarity comparisons — verses never
    do (owner decision 2026-07-10); everything else keeps today's behaviour."""
    return strain.get("role") != "verse"


def resolve_part_ref(tune: dict, ref: str) -> tuple[dict, int] | None:
    """Resolve a part id (or a bare strain name, unique-part strains only)
    to (strain, part_index); None when it matches nothing."""
    for strain in tune.get("strains") or []:
        ids = part_ids(strain)
        for i, pid in enumerate(ids):
            if pid == ref:
                return strain, i
        if strain.get("name") == ref and len(ids) == 1:
            return strain, 0
    return None


def resolve_anchor(tune: dict, anchor: dict) -> tuple[dict, dict, str]:
    """Resolve a {strain, part[, bar]} anchor (§3.3) to (strain, part,
    part_id). Raises ValueError with a loud message when it dangles."""
    name = (anchor or {}).get("strain")
    idx = (anchor or {}).get("part")
    strain = next((s for s in tune.get("strains") or []
                   if s.get("name") == name), None)
    if strain is None:
        raise ValueError(f"anchor names unknown strain {name!r}")
    parts = strain.get("parts") or []
    if not isinstance(idx, int) or not 0 <= idx < len(parts):
        raise ValueError(
            f"anchor part {idx!r} out of range for strain {name!r} "
            f"({len(parts)} parts)")
    part = parts[idx]
    bar = anchor.get("bar")
    if bar is not None and not (isinstance(bar, int)
                                and 1 <= bar <= len(part.get("bars") or [])):
        raise ValueError(
            f"anchor bar {bar!r} out of range for {name!r} part {idx} "
            f"({len(part.get('bars') or [])} bars)")
    return strain, part, part_ids(strain)[idx]


_STRAIN_NAME = re.compile(r"^[a-z][a-z0-9]*$")
# Numbered strains of a multi-strain rag/march (s1, s2, s3, …) are always a
# valid role-"strain" name — the book prints up to five per tune.
_NUMBERED_STRAIN = re.compile(r"^s\d+$")


def validate_strains(tune: dict) -> list[str]:
    """Structural validation of a strains-model tune (loud at edit time,
    Phase C §4/§7). Returns a list of error messages, empty when clean.
    Chord syntax is NOT checked here — expansion / the chord checker owns
    that; this guards the strain/part/anchor structure."""
    errors: list[str] = []
    strains = tune.get("strains")
    if not isinstance(strains, list) or not strains:
        return ["strains must be a non-empty list"]

    seen_names: set = set()
    for si, strain in enumerate(strains):
        if not isinstance(strain, dict):
            errors.append(f"strains[{si}] is not an object")
            continue
        name, role = strain.get("name"), strain.get("role")
        where = f"strain {name!r}" if name else f"strains[{si}]"
        if not isinstance(name, str) or not _STRAIN_NAME.match(name):
            errors.append(f"{where}: name must be lowercase "
                          f"([a-z][a-z0-9]*), got {name!r}")
        elif name in seen_names:
            errors.append(f"{where}: duplicate strain name")
        else:
            seen_names.add(name)
        if role not in ROLES:
            errors.append(f"{where}: role must be one of {ROLES}, got {role!r}")
        elif role == "chorus" and name != "chorus":
            errors.append(f"{where}: role 'chorus' requires name 'chorus'")
        elif role == "verse" and name != "verse":
            errors.append(f"{where}: role 'verse' requires name 'verse'")
        elif (role == "strain" and name not in NAMED_STRAINS - {"verse"}
                and not _NUMBERED_STRAIN.match(name)):
            errors.append(
                f"{where}: unknown named strain — allowed: "
                f"{', '.join(sorted(NAMED_STRAINS - {'verse'}))}, or a "
                "numbered strain s1, s2, s3, …. Rename the strain, or add "
                "it in code (NAMED_STRAINS + the displayers' STRAIN_TINT).")
        elif role == "aux" and name not in AUX_CONNECTORS:
            errors.append(
                f"{where}: unknown aux connector — allowed: "
                f"{', '.join(sorted(AUX_CONNECTORS))}")
        parts = strain.get("parts")
        if not isinstance(parts, list) or not parts:
            errors.append(f"{where}: parts must be a non-empty list")
            continue
        for pi, part in enumerate(parts):
            pwhere = f"{where} part {pi}"
            if not isinstance(part, dict):
                errors.append(f"{pwhere}: not an object")
                continue
            label = part.get("label")
            if not isinstance(label, str) or not label.strip():
                errors.append(f"{pwhere}: label must be a non-empty string")
            plays = part.get("plays", 1)
            if not isinstance(plays, int) or plays < 1:
                errors.append(f"{pwhere}: plays must be an int >= 1, "
                              f"got {plays!r}")
            bars = part.get("bars")
            if not isinstance(bars, list) or not bars:
                errors.append(f"{pwhere}: bars must be a non-empty list")
            elif not all(isinstance(b, dict) for b in bars):
                errors.append(f"{pwhere}: every bar must be an object")

    # Part ids must be unique tune-wide (map-keyed fields depend on it).
    ids = [pid for pid, _s, _p in iter_parts(tune)]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"duplicate part id {dup!r} — relabel one of the parts")

    # Anchors (§3.3) must resolve: variant targets and the coda jump-off.
    for vi, variant in enumerate(tune.get("variants") or []):
        for ti, target in enumerate(variant.get("targets") or []):
            try:
                resolve_anchor(tune, target)
            except ValueError as exc:
                errors.append(f"variants[{vi}].targets[{ti}]: {exc}")
    cj = tune.get("coda_jump")
    if cj:
        try:
            resolve_anchor(tune, cj.get("from") or {})
        except ValueError as exc:
            errors.append(f"coda_jump.from: {exc}")

    # Map-keyed per-part fields must reference real parts.
    for ref in (tune.get("section_keys") or {}):
        if resolve_part_ref(tune, ref) is None:
            errors.append(f"section_keys[{ref!r}] matches no part id")
    return errors


# ---------------------------------------------------------------------------
# Grid expansion and form flattening (spec §4.2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Slot:
    chord: Chord
    section: str
    bar: int      # 1-based bar number within the section
    half: int     # 0 = beat 1, 1 = beat 3 (mid-bar in 3/4)


def expand_section(name: str, bars: list[dict], prev: Chord | None = None
                   ) -> tuple[list[Slot], Chord | None]:
    """Expand one section to the fixed 2-slots-per-bar matching grid.

    `prev` is the chord carried in from the previous bar (continuation bars
    repeat it). Returns the slots and the chord carried out of the section.
    """
    slots: list[Slot] = []
    for i, bar in enumerate(bars):
        beats = bar.get("beats") or {}
        current = prev
        # Beat-by-beat sweep: slot 0 is whatever sounds at beat 1,
        # slot 1 whatever sounds at beat 3 (a beat-2 chord still sounds there).
        # A beat-4 chord is dropped from the grid but carries into the next bar.
        by_beat = {int(k): v for k, v in beats.items()}
        if 1 in by_beat:
            current = parse_chord(by_beat[1])
        if current is None:
            raise ChordParseError(
                f"section {name!r} bar {bar.get('bar', i + 1)} has no chord and "
                "nothing to carry over")
        slot0 = current
        for beat in (2, 3):
            if beat in by_beat:
                current = parse_chord(by_beat[beat])
        slot1 = current
        if 4 in by_beat:
            current = parse_chord(by_beat[4])
        barno = bar.get("bar", i + 1)
        slots.append(Slot(slot0, name, barno, 0))
        slots.append(Slot(slot1, name, barno, 1))
        prev = current
    return slots, prev


def expand_tune(tune: dict) -> dict[str, list[Slot]]:
    """Expand every playable unit of a tune, in document (= printed) order.

    Strains-model tunes expand one entry per part, keyed by the generated
    part id; legacy tunes (a `sections` map) expand per section key. A part
    with `plays: N` still expands once — the stored grid is the comparison
    and display unit, exactly as the old "identical parts stored once" rule.

    Variants are ignored (main text only). The carried chord flows across
    part boundaries in form order, so a continuation bar at the top of a
    part repeats the previous part's last chord.
    """
    out: dict[str, list[Slot]] = {}
    prev: Chord | None = None
    for name, bars in sections_view(tune).items():
        slots, prev = expand_section(name, bars, prev)
        out[name] = slots
    return out


def flatten(section_slots: dict[str, list[Slot]]) -> list[Slot]:
    """Concatenate section slot lists in form order (dict document order)."""
    return [s for slots in section_slots.values() for s in slots]


# ---------------------------------------------------------------------------
# Tonic-relative sequences (spec §4.3)
# ---------------------------------------------------------------------------

# A token is (degree, quality_class); degree is (root_pc - reference_pc) % 12,
# None only for N.C. slots. Plain tuples so sequences hash and shingle cheaply.
Token = tuple


def reference_pc(tonic: str, mode: str) -> int:
    """One shared pitch space (locked decision, spec §1): the reference is
    the tonic for major keys and the relative major's tonic for minor keys —
    majors read as if in C, minors as if in A minor."""
    pc = pitch_class(tonic)
    return pc if mode == "major" else (pc + 3) % 12


def _token(chord: Chord, ref_pc: int) -> Token:
    if not chord.is_sounding:
        return (None, "nc")
    return ((chord.root_pc - ref_pc) % 12, chord.quality)


@dataclass(frozen=True)
class SectionSeq:
    tokens: tuple      # Token sequence, local-relative when local_key is set
    start: int         # slot offset of this section inside full_seq
    local_key: dict | None  # {"tonic", "mode"} from Phase 0 section_keys


@dataclass(frozen=True)
class TuneSequences:
    full_seq: tuple                     # flattened form, global-key relative
    section_seqs: dict[str, SectionSeq]
    mode: str
    meter: str | None
    form: str | None
    bar_count: int


def tonic_relative(annotated: dict) -> TuneSequences:
    """Tonic-relative token sequences for one annotated tune (spec §4.3).

    `full_seq` keeps every degree relative to the tune's global `key`
    (modulations included — a modulating tune looking less similar at the
    whole-tune level is musically correct). Each section with a Phase 0
    `section_keys` entry gets its degrees computed against its *local* key
    and carries the `local_key` marker; every other section's sequence is
    the exact slice of `full_seq`.
    """
    key = annotated["key"]
    section_keys = annotated.get("section_keys") or {}
    global_ref = reference_pc(key["tonic"], key["mode"])
    sections = expand_tune(annotated)

    full_seq: list[Token] = []
    section_seqs: dict[str, SectionSeq] = {}
    for name, slots in sections.items():
        start = len(full_seq)
        global_tokens = tuple(_token(s.chord, global_ref) for s in slots)
        full_seq.extend(global_tokens)
        local = section_keys.get(name)
        if local:
            local_ref = reference_pc(local["tonic"], local["mode"])
            tokens = tuple(_token(s.chord, local_ref) for s in slots)
            marker = {"tonic": local["tonic"], "mode": local["mode"]}
        else:
            tokens, marker = global_tokens, None
        section_seqs[name] = SectionSeq(tokens, start, marker)

    return TuneSequences(tuple(full_seq), section_seqs, key["mode"],
                         annotated.get("time_signature"),
                         annotated.get("form"), len(full_seq) // 2)



# Named strains the displayers colour consistently (mirror of the displayer's
# STRAIN_TINT keys). validate_strains restricts a role-"strain" name to these
# — extend both together to introduce a new strain. NOT enforced in the
# digitizer pipeline (raw output may name arbitrary strains); display policy.
NAMED_STRAINS = frozenset({"verse", "intro", "thema", "impro", "interlude",
                           "coda", "part1", "part2", "s1", "s2", "blues"})

# ---------------------------------------------------------------------------
# Scale-degree naming (used for the `opening` field, spec §3.1)
# ---------------------------------------------------------------------------

_DEGREE_NAME = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV",
                6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
_LOWERCASE_QUALITIES = {"min", "m7b5", "dim"}


def degree_name(root_pc: int, tonic_pc: int, quality: str) -> str:
    """Roman numeral of `root_pc` relative to the tune's own tonic.

    Uppercase for maj/dom/aug/sus quality classes, lowercase for
    min/m7b5/dim; accidental prefix for non-diatonic roots (single shared
    major-scale-based table for both modes, so F minor's ii reads 'ii' and
    its relative major reads 'bIII').
    """
    name = _DEGREE_NAME[(root_pc - tonic_pc) % 12]
    if quality in _LOWERCASE_QUALITIES:
        # lowercase the letters, keep the accidental prefix as-is
        name = "".join(c.lower() if c in "IV" else c for c in name)
    return name


def compute_opening(tune: dict, tonic: str, mode: str) -> dict | None:
    """The `opening` field: first sounding chord of the flattened form,
    expressed relative to the resolved key. Purely computed — no voter, no
    LLM. Returns None when the tune has no sounding chord at all.
    """
    for slot in flatten(expand_tune(tune)):
        ch = slot.chord
        if ch.is_sounding:
            return {
                "degree": degree_name(ch.root_pc, pitch_class(tonic), ch.quality),
                "quality": ch.quality,
                "chord": ch.symbol,
            }
    return None
