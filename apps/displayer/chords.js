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
    // end of the token — "F6/9" is a quality, "Am7/Eb" is a bass note.
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

  /* Spec §7.2: maj→Δ, m7b5→ø7, minor m→-, accidentals→music glyphs. */
  function displayQuality(q) {
    let s = q;
    s = s.replace(/m7b5/g, "ø7"); // ø7
    s = s.replace(/maj/g, "Δ"); // Δ (consume maj's m before the minor rule)
    s = s.replace(/m/g, "-"); // remaining m = minor, "-" saves width
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

  /* Render a chord token as an HTML string. Unparseable tokens render
   * verbatim (safety net — verified files pass the syntax checker). */
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

    // The accidental (top) and quality (bottom) share a column to the right of
    // the letter, so the quality sits directly below the sharp/flat instead of
    // trailing after it (E♭ø7, A♭-7) — keeping the chord a letter-width wide.
    let html = `<span class="root">${c.letter}</span>`;
    let tail = "";
    if (c.acc) tail += `<span class="acc">${displayAccidental(c.acc)}</span>`;
    if (c.quality) {
      const { main, stack } = splitQuality(displayQuality(c.quality));
      let q = "";
      if (main) q += `<span class="qual-main">${escapeHtml(main)}</span>`;
      if (stack.length) {
        q += '<span class="qual-stack">' +
          stack.map((s) => `<span>${escapeHtml(s)}</span>`).join("") +
          "</span>";
      }
      tail += `<span class="qual">${q}</span>`;
    } else if (c.acc) {
      // Bare accidental triad (C♯, A♭): reserve the lower row so the
      // accidental floats up as a superscript.
      tail += '<span class="qual qual-empty"></span>';
    }
    if (tail) html += `<span class="tail">${tail}</span>`;
    if (c.bass) {
      html += `<span class="bass">/${c.bass.letter}${displayAccidental(c.bass.acc)}</span>`;
    }
    return `<span class="${cls}">${open}${html}${close}</span>`;
  }

  const api = { parseChord, displayQuality, splitQuality, renderChordHTML, escapeHtml };
  global.GrillesChords = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
