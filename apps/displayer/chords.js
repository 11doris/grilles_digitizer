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

  /* Spec §7.2: maj→Δ, m7b5→ø7, accidentals→music glyphs. */
  function displayQuality(q) {
    let s = q;
    s = s.replace(/m7b5/g, "ø7"); // ø7
    s = s.replace(/maj/g, "Δ"); // Δ
    s = s.replace(/#/g, SHARP);
    s = s.replace(/b(?=\d)/g, FLAT);
    return s;
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

    let html = `<span class="root">${c.letter}`;
    if (c.acc) html += `<span class="acc">${displayAccidental(c.acc)}</span>`;
    html += "</span>";
    if (c.quality) html += `<span class="qual">${escapeHtml(displayQuality(c.quality))}</span>`;
    if (c.bass) {
      html += `<span class="bass">/${c.bass.letter}${displayAccidental(c.bass.acc)}</span>`;
    }
    return `<span class="${cls}">${open}${html}${close}</span>`;
  }

  const api = { parseChord, displayQuality, renderChordHTML, escapeHtml };
  global.GrillesChords = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
