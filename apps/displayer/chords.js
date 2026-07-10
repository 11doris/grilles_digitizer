/* Chord token parsing and HTML rendering (spec §7). */
"use strict";

(function (global) {
  const FLAT = "♭"; // ♭
  const SHARP = "♯"; // ♯

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* True when the parenthesis opening at index 0 closes at the last index. */
  function outerParensBalanced(s) {
    let depth = 0;
    for (let i = 0; i < s.length; i++) {
      if (s[i] === "(") depth++;
      else if (s[i] === ")") {
        depth--;
        if (depth === 0) return i === s.length - 1;
      }
    }
    return false;
  }

  /*
   * Parse a chord token into
   *   { optional, nc } for N.C.
   *   { optional, letter, acc, quality, bass: {letter, acc}|null }
   * Returns null when the token is not a valid chord.
   */
  function parseChord(raw) {
    let s = String(raw).trim();
    let optional = false;
    if (s.startsWith("(") && s.endsWith(")") && outerParensBalanced(s)) {
      optional = true;
      s = s.slice(1, -1).trim();
    }
    if (s === "N.C.") return { optional, nc: true };

    const rootMatch = s.match(/^([A-G])(#|b)?/);
    if (!rootMatch) return null;
    let rest = s.slice(rootMatch[0].length);

    // A "/" only introduces a bass note when followed by a note letter at the
    // end of the token — "Am7/Eb" is a bass note. (Six-nine chords are written
    // slashless as F69, so no quality contains a "/".)
    let bass = null;
    const bassMatch = rest.match(/\/([A-G])(#|b)?$/);
    if (bassMatch) {
      bass = { letter: bassMatch[1], acc: bassMatch[2] || null };
      rest = rest.slice(0, bassMatch.index);
    }

    // A bare "(b9)" on a root is shorthand for a dominant flat-nine — render it
    // as "7b9" (e.g. "Bb(b9)" → "Bb7b9").
    if (rest === "(b9)") rest = "7b9";

    return {
      optional,
      letter: rootMatch[1],
      acc: rootMatch[2] || null,
      quality: rest,
      bass,
    };
  }

  function displayAccidental(acc) {
    if (acc === "#") return SHARP;
    if (acc === "b") return FLAT;
    return "";
  }

  /* Spec §7.2: maj→Δ, m7b5→ø7, minor m→–, accidentals→music glyphs. */
  function displayQuality(q) {
    let s = q;
    s = s.replace(/m7b5/g, "ø7"); // ø7
    s = s.replace(/maj/g, "Δ"); // Δ (consume maj's m before the minor rule)
    //s = s.replace(/m/g, "–"); // remaining m = minor → en dash, saves width
    s = s.replace(/#/g, SHARP);
    s = s.replace(/b(?=\d)/g, FLAT);
    return s;
  }

  /* Split a parenthesised group's contents into individual alterations when it
   * is a run of accidental+number tokens ("♯5♯9" → ["♯5","♯9"]); otherwise keep
   * it whole ("13", "Δ7" stay single). */
  function splitAlterations(inner) {
    const toks = inner.match(/[♯♭]\d+/g);
    if (toks && toks.join("") === inner) return toks;
    return inner ? [inner] : [];
  }

  /*
   * Split a display quality (glyphs already substituted) into the baseline core
   * and a list of tensions/alterations to stack vertically (Figure D.3). Keeps
   * the chord's primary symbol/number inline and lifts the added tensions —
   * parenthesised groups and trailing altered fifths/ninths/…—into the stack,
   * so wide chords like "7(13)" or "(♯5♯9)" collapse to one narrow column.
   */
  function splitQuality(q) {
    const stack = [];
    // Parenthesised additions → stacked, left to right.
    let main = q.replace(/\(([^)]*)\)/g, (_, inner) => {
      splitAlterations(inner).forEach((t) => stack.push(t));
      return "";
    });
    // Trailing altered tones ("7♯5" → "7" + "♯5"); the primary number stays.
    const trailing = [];
    let m;
    while ((m = main.match(/[♯♭](?:5|6|9|11|13)$/))) {
      trailing.unshift(m[0]);
      main = main.slice(0, m.index);
    }
    return { main, stack: trailing.concat(stack) };
  }

  /* Escape a raw string, then enlarge every flat glyph — the ♭ character runs
   * noticeably smaller than ♯ in the fallback music font, so it gets its own
   * span the CSS bumps up (spec §7.2). */
  function withFlats(text) {
    return escapeHtml(text).split(FLAT).join(`<span class="fl">${FLAT}</span>`);
  }

  /*
   * Render a chord token as an HTML string, laid out as a fixed box grid
   * (Figure D.3): a large root letter on the left, a middle column stacking the
   * root accidental (top) over the core quality (bottom), and a right column
   * holding up to two alterations (alt-up / alt-down). Every box sits at a fixed
   * position so the root shares one ground line across chords regardless of
   * accidental or tensions (e.g. B7♯11 and B♭7♯11 align). Unparseable tokens
   * render verbatim (safety net — verified files pass the syntax checker).
   */
  function renderChordHTML(raw) {
    const c = parseChord(raw);
    if (!c) {
      return `<span class="chord chord-raw" title="unrecognized chord">${escapeHtml(raw)}</span>`;
    }

    const cls = c.optional ? "chord optional" : "chord";
    const open = c.optional ? '<span class="paren">(</span>' : "";
    const close = c.optional ? '<span class="paren">)</span>' : "";

    if (c.nc) {
      return `<span class="${cls}">${open}<span class="nc">N.C.</span>${close}</span>`;
    }

    let main = "";
    let stack = [];
    if (c.quality) {
      ({ main, stack } = splitQuality(displayQuality(c.quality)));
    }

    // The box holds the root + its accidental/quality/alteration grid; the
    // parentheses and bass note sit outside it as flex siblings.
    let box = `<span class="root">${escapeHtml(c.letter)}</span>`;
    // Middle column: root accidental (top box) over the core quality (bottom).
    if (c.acc) {
      const kind = c.acc === "b" ? "flat" : "sharp";
      box += `<span class="acc ${kind}">${displayAccidental(c.acc)}</span>`;
    }
    if (main) box += `<span class="qual">${withFlats(main)}</span>`;
    // Right column: alterations, lone one in the lower box, a pair straddling.
    if (stack.length === 1) {
      box += `<span class="alt-down">${withFlats(stack[0])}</span>`;
    } else if (stack.length) {
      box += `<span class="alt-up">${withFlats(stack[0])}</span>`;
      box += `<span class="alt-down">${withFlats(stack[1])}</span>`;
    }
    let html = `<span class="box">${box}</span>`;
    if (c.bass) {
      html += `<span class="bass">${withFlats("/" + c.bass.letter + displayAccidental(c.bass.acc))}</span>`;
    }
    return `<span class="${cls}">${open}${html}${close}</span>`;
  }

  /* ---------------------------------------------------------- transposition
   *
   * Transposing a chart moves only the absolute pitches — the root and the
   * slash bass. Everything else in a chord symbol (quality, extensions and
   * alterations like "m7b5", "7#5", "b9", "sus4") is written relative to the
   * root, so it is carried over verbatim. We therefore rewrite just the two
   * note letters in the raw symbol and leave the rest of the string untouched,
   * which keeps the exact printed quality text intact.
   *
   * Spelling is table-driven (§7.2): the caller passes a 12-entry pitch-class →
   * name table chosen for the target key's flat/sharp bias, so a chart in G♭ is
   * spelled with flats (G♭, not F♯) and a sharp key with sharps. This always
   * yields clean single-accidental names — never C♭/F♯-in-a-flat-key,
   * double-accidentals or the like.
   */
  const LETTER_PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

  /* "Bb" -> 10, "F#" -> 6. */
  function pitchClass(name) {
    let pc = LETTER_PC[name[0]];
    for (const acc of name.slice(1)) pc += acc === "#" ? 1 : acc === "b" ? -1 : 0;
    return ((pc % 12) + 12) % 12;
  }

  // Flat- and sharp-biased spellings; pick per target key (§7.2). "Gb" not "F#".
  const FLAT_SPELL = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const SHARP_SPELL = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

  function transposeNote(letter, acc, shift, spell) {
    const pc = (pitchClass(letter + (acc || "")) + shift) % 12;
    return spell[((pc % 12) + 12) % 12];
  }

  /* Transpose one printed chord symbol up `shift` semitones, spelling roots via
   * the `spell` table. A shift of 0 (or N.C./unparseable tokens) returns the
   * symbol unchanged. */
  function transposeChordSymbol(raw, shift, spell) {
    let s = String(raw);
    if (!shift) return s;
    // Root: the first note letter, possibly behind an opening "(" (optional chord).
    const m = /^(\(?)([A-G])(#|b)?/.exec(s);
    if (!m) return s; // N.C. or anything without a leading note letter
    s = m[1] + transposeNote(m[2], m[3], shift, spell) + s.slice(m[0].length);
    // Slash bass: a note letter after "/" at the very end (before a closing ")"
    // of an optional chord and/or a trailing "?" uncertainty marker).
    s = s.replace(/\/([A-G])(#|b)?(?=\)?\??$)/,
      (_full, bl, ba) => "/" + transposeNote(bl, ba, shift, spell));
    return s;
  }

  /* -------------------------------------------------- scale-degree naming
   *
   * Mirrors pipelines/chords/similarity/normalize.py exactly (§4.1 quality
   * reduction + §3.1 degree naming); the shared fixture generated by
   * pipelines/chords/similarity/js_fixture.py pins the two implementations
   * together (spec §8.3) so they cannot drift silently.
   */
  const STEM_CLASS = {
    "": "maj", "6": "maj", "maj7": "maj", "maj9": "maj", "69": "maj",
    "m": "min", "m6": "min", "m7": "min", "m9": "min", "m11": "min",
    "m13": "min", "m69": "min", "m(maj7)": "min",
    "7": "dom", "9": "dom", "11": "dom", "13": "dom",
    "m7b5": "m7b5",
    "o7": "dim",
    "sus4": "sus", "sus2": "sus", "7sus4": "sus", "9sus4": "sus",
  };
  const STEMS_DESC = Object.keys(STEM_CLASS)
    .sort((a, b) => b.length - a.length)
    .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const CORE_RE = new RegExp(
    "^([A-G](?:#|b)?)" +                    // root
    "(" + STEMS_DESC.join("|") + ")" +      // stem (longest first)
    "(\\((?:6|7|9|11|13)\\))?" +            // parenthesised extension
    "(alt)?" +                              // 'alt'
    "((?:b5|#5|b9|#9|#11|b13)*)" +          // bare alterations
    "(\\((?:b5|#5|b9|#9|#11|b13)+\\))?" +   // parenthesised alterations
    "(/[A-G](?:#|b)?)?" +                   // slash bass
    "(\\?)?$"                               // uncertainty marker
  );

  /* symbol → {rootPc, quality} with the §4.1 quality class, or null for
     N.C./unparseable. Accepts the same vocabulary as the Python parser. */
  function chordClass(raw) {
    let s = String(raw).trim();
    if (s.startsWith("(") && s.endsWith(")") && outerParensBalanced(s)) {
      s = s.slice(1, -1).trim();
    }
    if (s === "N.C.") return null;
    const m = CORE_RE.exec(s);
    if (!m) return null;
    const [, root, stem, pext, altw, balts, palts] = m;
    let quality = STEM_CLASS[stem];
    const alts = (balts || "") + (palts || "").replace(/[()]/g, "");
    // Alteration-driven reclassification of bare triads (normalize.py):
    if (quality === "maj" && stem === "" && !pext) {
      if (/b9|#9|#11|b13/.test(alts)) quality = "dom";
      else if (alts.includes("#5")) quality = "aug";
    }
    if (quality === "maj" && stem === "" && pext && pext !== "(6)") quality = "dom";
    if (altw) quality = "dom";
    return { rootPc: pitchClass(root), quality };
  }

  const DEGREE_NAME = ["I", "bII", "II", "bIII", "III", "IV",
    "#IV", "V", "bVI", "VI", "bVII", "VII"];
  const LOWERCASE_QUALITIES = new Set(["min", "m7b5", "dim"]);

  /* Roman numeral of rootPc relative to tonicPc — mirror of
     normalize.degree_name (uppercase maj/dom/aug/sus, lowercase
     min/m7b5/dim, accidental prefix for non-diatonic roots). */
  function degreeName(rootPc, tonicPc, quality) {
    let name = DEGREE_NAME[(((rootPc - tonicPc) % 12) + 12) % 12];
    if (LOWERCASE_QUALITIES.has(quality)) {
      name = name.split("").map((c) => "IV".includes(c) ? c.toLowerCase() : c).join("");
    }
    return name;
  }

  /* Printed symbol → roman degree relative to tonicPc; null for N.C. */
  function chordDegree(raw, tonicPc) {
    const c = chordClass(raw);
    return c ? degreeName(c.rootPc, tonicPc, c.quality) : null;
  }

  const api = { parseChord, displayQuality, splitQuality, renderChordHTML, escapeHtml,
    pitchClass, transposeChordSymbol, FLAT_SPELL, SHARP_SPELL,
    chordClass, degreeName, chordDegree };
  global.GrillesChords = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
