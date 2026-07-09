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

  const api = { parseChord, displayQuality, splitQuality, renderChordHTML, escapeHtml };
  global.GrillesChords = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
