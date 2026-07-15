/* ═══════════════════════════════════════════════════════════════════════════
   Tune Verifier — app.js
   ══════════════════════════════════════════════════════════════════════════ */

// ─── Helpers ─────────────────────────────────────────────────────────────────
const qs  = (sel, ctx = document) => ctx.querySelector(sel);
const qsa = (sel, ctx = document) => ctx.querySelectorAll(sel);

/** HTML-escape a value for use in an attribute or text node. */
function esc(val) {
  return String(val == null ? '' : val)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ─── State ────────────────────────────────────────────────────────────────────
const S = {
  tunes:     [],              // [{id, title, verified, deferred, status, has_image}]
  filter:    'needs_review',  // active sidebar tab: needs_review | deferred | all
  currentId: null,            // ID of open tune
  data:      null,            // tune data; parts is the flat editing view of strains
  config:    { roles: ['chorus', 'verse', 'strain', 'aux'],
               named_strains: [], aux_connectors: [] }, // from /api/tunes
  dirty:     false,
};

// Sidebar glyph per review state.
const STATUS_GLYPH = { verified: '✓', deferred: '⏸', needs_review: '○' };

/** The review state of a tune list entry. */
function tuneStatus(t) {
  if (t.verified) return 'verified';
  if (t.deferred) return 'deferred';
  return 'needs_review';
}

// Known meta fields shown in the grid (in display order)
const KNOWN_META = [
  'title', 'composer', 'year', 'style',
  'tempo', 'form', 'time_signature', 'page', 'source',
];

// Legacy derived fields (pre-Phase C); dropped from any older WIP file on
// save and never shown — the structure itself carries labels and repeats now.
const DERIVED_META = ['form_strains', 'section_labels'];

// ─── Beat count ───────────────────────────────────────────────────────────────
function getBeatCount() {
  const ts = S.data?.time_signature || '4/4';
  const m  = String(ts).match(/^(\d+)\s*\//);
  const n  = m ? parseInt(m[1], 10) : 4;
  return Math.max(1, Math.min(16, n));
}

// ─── API calls ────────────────────────────────────────────────────────────────
async function apiFetch(url, opts = {}) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

function apiPutState(patch) {
  apiFetch('/api/state', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }).catch(() => {});
}

// ─── Toast ────────────────────────────────────────────────────────────────────
let _toastTimer;
function toast(msg, type = 'info') {
  const el = qs('#toast');
  el.textContent = msg;
  el.className = `toast-${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.add('hidden'), 3200);
}

// ─── Chord validation ─────────────────────────────────────────────────────────
// Grammar mirrors grilles_digitizer/prompt.py (=== CHORD NOTATION ===) and
// tools/check_chord_syntax.py — keep the three in sync.
const CHORD_ROOT = '[A-G](?:#|b)?';
const CHORD_ALT  = '(?:b5|#5|b9|#9|#11|b13)';
const CHORD_STEMS = [
  '', 'm',
  '6', '7', '9', '11', '13',
  '69',
  'maj7', 'maj9',
  'm6', 'm7', 'm9', 'm11', 'm13',
  'm69',
  'm7b5',
  'o7',
  'm\\(maj7\\)',
  'sus4', 'sus2', '7sus4', '9sus4',
];

const CHORD_CORE_RE = new RegExp(
  '^(?<root>' + CHORD_ROOT + ')' +
  '(?<stem>' + CHORD_STEMS.slice().sort((a, b) => b.length - a.length).join('|') + ')' +
  '(?<pext>\\((?:6|7|9|11|13)\\))?' +      // parenthesised superscript ext, e.g. 7(13)
  '(?<altw>alt)?' +                        // altered dominant, e.g. F7alt (needs a 7th/extension)
  '(?<balts>' + CHORD_ALT + '*)' +         // bare alterations (need a 7th/extension)
  '(?<palts>\\(' + CHORD_ALT + '+\\))?' +  // parenthesised alterations (bare triad)
  '(?<slash>/(?:' + CHORD_ROOT + '|[2-7]))?' +  // bass note (F/Bb) or degree in the bass (F/5)
  '(?<unc>\\?)?$'
);

const CHORD_HAS_EXT = /(?:6|7|9|11|13)/;
const CHORD_ALT_DEGREE = { b5: 5.0, b9: 9.0, '#9': 9.5, '#11': 11.0, '#5': 12.5, b13: 13.0 };

// A bare-triad flat-nine as the WHOLE quality — the canonical "(b9)", plus the
// raw printed spellings "(9b)" and "9b" — denotes a dominant flat-nine, so the
// verifier presents and saves it as "7b9" (e.g. "Bb(b9)" → "Bb7b9"), the same
// reading the displayer applies (apps/displayer/chords.js). "9b5" (a flat-5 nine
// chord) is deliberately left alone. NOTE: this is an intentional divergence from
// prompt.py, whose canonical spelling for the digitizer stays "(b9)".
const BARE_FLAT9_RE = /^(?:\(b9\)|\(9b\)|9b)$/;

/** Targeted hints for strings the grammar rejects. */
function chordParseHints(s) {
  const hints = [];
  if (/^[-%•←]+$|^\.\/\.$|^\/\/$/.test(s))
    hints.push('Repeat shorthand must be expanded — write the actual chord it stands for.');
  if (/^[a-g]/.test(s))
    hints.push('The root letter must be uppercase: A–G (optionally followed by # or b).');
  if (/^H/.test(s))
    hints.push('German "H" is written B.');
  if (/9b(?!5)|\(9b\)/.test(s))
    hints.push('A bare-triad flat-nine is written "7b9" — e.g. E7b9 (typed "(b9)"/"(9b)"/"9b" convert on blur).');
  if (/7M|M7|Δ|∆/.test(s))
    hints.push('Major 7th is written maj7 (7M / M7 / Δ → maj7), e.g. Ebmaj7.');
  if (/ø|Ø/.test(s))
    hints.push('Half-diminished is written m7b5 (ø → m7b5), e.g. Am7b5.');
  if (/[o0°ºΟO˚]\)?$/.test(s))
    hints.push('Diminished is written o7 (lowercase o + 7), e.g. G#o7.');
  if (/aug|\+/.test(s))
    hints.push('Augmented: (#5) on a bare triad (Eb(#5)), #5 after a 7th/extension (Eb7#5) or after m (Bbm#5); 5+ → #5.');
  if (/sus(?![24])/.test(s))
    hints.push('Suspended is sus4 / sus2 / 7sus4 / 9sus4 — a printed bare "sus" means sus4.');
  if (/min|MIN/.test(s))
    hints.push('Minor is written m (Cm, Cm7), not "min".');
  if (/-/.test(s) && !/^[-%•←]+$/.test(s))
    hints.push('Do not use "-" inside a chord; minor is written m.');
  if (/maj(?![79])/.test(s))
    hints.push('"maj" must be followed by 7 or 9 (maj7, maj9); a plain major triad is the bare root.');
  if (/m(?:maj|M)7|m7M/.test(s))
    hints.push('Minor-major 7th needs parens: m(maj7), e.g. Dm(maj7) — never "Dmmaj7".');
  if (!hints.length)
    hints.push('Not a recognised chord. Expected: ROOT(A–G, #/b) + quality (m, maj7, m7b5, o7, '
      + 'm(maj7), sus4…) + extension (6, 7, 9, 11, 13, 69) + alterations (b5 b9 #9 #11 #5 b13) '
      + 'or alt + optional /bass (a note Fm7/Bb, or a degree 2–7 in the bass, F/5) and '
      + 'trailing ? — e.g. Bb7, Fm7b5, C9b5, F7alt, F(#5), D(b9), Fm7/Bb, F/5.');
  return hints;
}

/** Check one core chord (no outer optional-parens). Returns error messages. */
function chordCoreErrors(s) {
  const m = CHORD_CORE_RE.exec(s);
  if (!m) return chordParseHints(s);
  const errs = [];
  const { root, stem, pext, altw, balts, palts } = m.groups;
  const hasExt = CHORD_HAS_EXT.test(stem) || !!pext;
  if (altw) {
    if (!hasExt)
      errs.push(`"alt" needs a 7th/extension — e.g. ${root}7alt.`);
    if (balts || palts)
      errs.push('"alt" cannot be combined with explicit alterations — use one or the other.');
  }
  const minorAug = stem === 'm' && !hasExt && !altw;
  if (balts && palts)
    errs.push('Mixes bare and parenthesised alterations — use one style.');
  if (balts && !hasExt && !(minorAug && balts === '#5'))
    errs.push(`Alteration on a bare triad must be parenthesised: ${root}${stem}(${balts}).`);
  if (palts && hasExt)
    errs.push(`A 7th/extension is present, so write the alteration bare: ${root}${stem}${pext || ''}${palts.slice(1, -1)}.`);
  if (palts === '(#5)' && minorAug)
    errs.push(`A minor triad's #5 is written bare: ${root}m#5, not ${root}m(#5).`);
  for (const group of [balts || '', (palts || '').replace(/[()]/g, '')]) {
    const alts = group.match(new RegExp(CHORD_ALT, 'g')) || [];
    const degs = alts.map(a => CHORD_ALT_DEGREE[a]);
    if (degs.some((d, i) => i > 0 && d < degs[i - 1]))
      errs.push(`Alterations must be in canonical order (b5 b9 #9 #11 #5 b13): ${alts.join(' ')}.`);
    if (new Set(alts).size !== alts.length)
      errs.push(`Duplicate alteration: ${alts.join(' ')}.`);
  }
  return errs;
}

/** Validate a chord string. Returns [] when valid (empty is valid = no chord). */
function chordErrors(raw) {
  const s = String(raw ?? '').trim();
  if (!s || s === 'N.C.') return [];
  if (/^n\.?c\.?$/i.test(s)) return ['An empty / no-chord bar is written exactly "N.C.".'];
  if (/\s/.test(s)) return ['A chord contains no spaces.'];
  // Whole optional chord in parens, e.g. (G7) or (A(b9)): validate the inside.
  if (s.startsWith('(') && s.endsWith(')')) {
    const inner = s.slice(1, -1);
    let depth = 0, wraps = true;
    for (const c of inner) {
      if (c === '(') depth++;
      else if (c === ')' && --depth < 0) { wraps = false; break; }
    }
    if (wraps) return chordCoreErrors(inner);
  }
  return chordCoreErrors(s);
}

/** Rewrite a bare-triad flat-nine ("(b9)", "(9b)", "9b") to the "7b9" spelling,
    preserving an optional-chord wrapper, a /bass, and a trailing "?". Any other
    string (including "9b5" and an already-"7b9" chord) is returned unchanged, so
    the function is idempotent and safe to run on every value. */
function normalizeChord(raw) {
  const s = String(raw ?? '').trim();
  if (!s) return s;
  // Whole optional chord in parens, e.g. "(A(b9))": normalize the inside, re-wrap.
  if (s.startsWith('(') && s.endsWith(')')) {
    const inner = s.slice(1, -1);
    let depth = 0, wraps = true;
    for (const c of inner) {
      if (c === '(') depth++;
      else if (c === ')' && --depth < 0) { wraps = false; break; }
    }
    if (wraps) return `(${normalizeChord(inner)})`;
  }
  const rootM = s.match(new RegExp('^' + CHORD_ROOT));
  if (!rootM) return s;
  const root = rootM[0];
  let rest = s.slice(root.length);
  // Peel a trailing "?" then a /bass so the quality stands alone for the test.
  let tail = '';
  if (rest.endsWith('?')) { tail = '?'; rest = rest.slice(0, -1); }
  const bassM = rest.match(new RegExp('/(?:' + CHORD_ROOT + '|[2-7])$'));
  if (bassM) { tail = bassM[0] + tail; rest = rest.slice(0, bassM.index); }
  if (BARE_FLAT9_RE.test(rest)) rest = '7b9';
  return root + rest + tail;
}

// ── Live validation UI ──
function chordHintEl() {
  let el = qs('#chord-hint');
  if (!el) {
    el = document.createElement('div');
    el.id = 'chord-hint';
    el.className = 'hidden';
    document.body.appendChild(el);
  }
  return el;
}

function showChordHint(input, errs) {
  const el = chordHintEl();
  el.innerHTML = errs.map(m => `<div>${esc(m)}</div>`).join('');
  const r = input.getBoundingClientRect();
  el.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 380))}px`;
  el.style.top  = `${r.bottom + 4}px`;
  el.classList.remove('hidden');
}

function hideChordHint() {
  qs('#chord-hint')?.classList.add('hidden');
}

/** Flag one beat input; show the hint bubble when it has focus. */
function validateChordInput(el, withHint = false) {
  const errs = chordErrors(el.value);
  el.classList.toggle('chord-invalid', errs.length > 0);
  el.title = errs.join('\n');
  if (withHint) {
    if (errs.length) showChordHint(el, errs);
    else hideChordHint();
  }
  return errs;
}

function validateAllChords() {
  qsa('.beat-inp').forEach(el => validateChordInput(el));
}

/** On blur, rewrite a bare-triad flat-nine in one beat input to its "7b9" form. */
function normalizeBeatInput(el) {
  const norm = normalizeChord(el.value);
  if (norm !== el.value.trim()) {
    el.value = norm;
    setDirty();
    validateChordInput(el);
  }
}

/** All invalid chords in a save payload (strains + variants), as readable strings. */
function invalidChordList(payload) {
  const out = [];
  const walk = (bars, where) => (bars || []).forEach(b => {
    Object.entries((b && b.beats) || {}).forEach(([beat, ch]) => {
      if (chordErrors(ch).length) out.push(`${where} bar ${b.bar} beat ${beat}: "${ch}"`);
    });
  });
  (payload.strains || []).forEach(s => (s.parts || []).forEach((p, i) =>
    walk(p.bars, `${s.name} ${p.label || `part ${i + 1}`}`)));
  (Array.isArray(payload.variants) ? payload.variants : [])
    .forEach((v, i) => walk(v?.bars, `variant ${i + 1}`));
  return out;
}

/** Variant targets that name a missing strain/part or run past the part's end. */
function invalidTargetList(payload) {
  const out = [];
  const byName = {};
  (payload.strains || []).forEach(s => { byName[s.name] = s.parts || []; });
  (Array.isArray(payload.variants) ? payload.variants : []).forEach((v, i) => {
    const nb = (v.bars || []).length;
    (Array.isArray(v.targets) ? v.targets : []).forEach(tg => {
      const parts = byName[tg.strain];
      const part  = parts && parts[tg.part];
      if (!part) {
        out.push(`variant ${i + 1}: unknown anchor ${tg.strain}/${tg.part}`);
      } else if (tg.bar < 1 || (tg.bar - 1 + nb) > (part.bars || []).length) {
        out.push(`variant ${i + 1}: ${tg.strain}[${tg.part}] bar ${tg.bar} + ${nb} bars runs past the part`);
      }
    });
  });
  return out;
}

// ─── Dirty tracking ───────────────────────────────────────────────────────────
function setDirty() {
  if (!S.dirty) {
    S.dirty = true;
    qs('#dirty-dot').classList.remove('hidden');
    if (S.currentId) apiPutState({ in_progress: S.currentId });
  }
}

function clearDirty() {
  S.dirty = false;
  qs('#dirty-dot').classList.add('hidden');
}

// ─── Progress ─────────────────────────────────────────────────────────────────
function updateProgress() {
  const total    = S.tunes.length;
  const done     = S.tunes.filter(t => t.verified).length;
  const deferred = S.tunes.filter(t => tuneStatus(t) === 'deferred').length;
  const parts = [`${done} / ${total} verified`];
  if (deferred) parts.push(`${deferred} deferred`);
  qs('#progress-text').textContent = parts.join(' · ');
  qs('#progress-fill').style.width = total > 0 ? `${(done / total) * 100}%` : '0%';
}

// ─── Filter tabs ──────────────────────────────────────────────────────────────
/** Tunes visible under the active filter, keeping source order. */
function visibleTunes() {
  if (S.filter === 'needs_review')   return S.tunes.filter(t => tuneStatus(t) === 'needs_review');
  if (S.filter === 'deferred')       return S.tunes.filter(t => tuneStatus(t) === 'deferred');
  return S.tunes;
}

function setFilter(f) {
  S.filter = f;
  qsa('#filter-tabs button').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────
function renderSidebar() {
  const vis = visibleTunes();
  qs('#tune-list').innerHTML = vis.map(t => {
    const st = tuneStatus(t);
    return `
    <li class="tune-item status-${st}${t.id === S.currentId ? ' active' : ''}"
        data-id="${esc(t.id)}">
      <span class="tune-status">${STATUS_GLYPH[st]}</span>
      <span class="tune-title">${esc(t.title || t.id)}</span>
    </li>`;
  }).join('') || '<li class="tune-empty">No tunes in this view</li>';
  updateProgress();
}

/** After a status change, refresh the list/buttons and advance out of an
    emptied slot: if the current tune left the active filter, open the next
    visible one (by source order). */
function afterStatusChange() {
  const vis = visibleTunes();
  if (S.currentId && !vis.some(t => t.id === S.currentId)) {
    const pos  = S.tunes.findIndex(t => t.id === S.currentId);
    const next = vis.find(t => S.tunes.indexOf(t) > pos) || vis[0];
    renderSidebar();
    if (next) { openTune(next.id); return; }
  }
  renderSidebar();
  updateActionButtons();
}

// ─── Data conversion (strains ⇄ flat parts) ──────────────────────────────────
/* The editor works on a FLAT list of parts — one card per part, exactly like
   the old one-card-per-section editor — each carrying its strain name, role,
   printed label (primes kept) and plays count. Consecutive cards sharing a
   strain name regroup into one strain on save (strains never interleave). */

/** strains [{name, role, parts:[{label, plays, bars}]}] → flat parts. */
function strainsToParts(strains) {
  const parts = [];
  (strains || []).forEach(s => (s.parts || []).forEach(p => parts.push({
    strain: s.name,
    role:   s.role,
    label:  p.label != null ? String(p.label) : '',
    plays:  p.plays || 1,
    bars:   (p.bars || []).map(b => ({
      bar:   b.bar,
      beats: Object.assign({}, b.beats || {}),
    })),
  })));
  return parts;
}

/** Flat parts → strains, consecutive same-name cards merged; bars renumbered.
    The run's role comes from its first card. */
function partsToStrains(parts) {
  const strains = [];
  (parts || []).forEach(p => {
    let last = strains[strains.length - 1];
    if (!last || last.name !== p.strain) {
      last = { name: p.strain, role: p.role, parts: [] };
      strains.push(last);
    }
    const part = { label: p.label };
    if (p.plays > 1) part.plays = p.plays;
    part.bars = p.bars.map((bar, i) => ({ bar: i + 1, beats: bar.beats }));
    last.parts.push(part);
  });
  return strains;
}

/** Consecutive same-strain runs over S.data.parts: [{name, role, start, parts}]. */
function partRuns(parts) {
  const runs = [];
  (parts || []).forEach(p => {
    const last = runs[runs.length - 1];
    if (!last || last.name !== p.strain) {
      runs.push({ name: p.strain, role: p.role, start: runs.length
        ? runs[runs.length - 1].start + runs[runs.length - 1].parts.length : 0,
        parts: [] });
    }
    runs[runs.length - 1].parts.push(p);
  });
  return runs;
}

/** A part id fragment from a printed label (mirror of normalize._label_base). */
function labelBase(label) {
  return String(label ?? '').trim().replace(/['’]+$/, '').replace(/\s+/g, '') || 'P';
}

/** Generated part ids for the flat list (mirror of normalize.part_ids):
    chorus parts read as letters ("A", "A1"), other strains prefix their name,
    a single-part aux connector is its bare name. */
function partIdList(parts) {
  const out = [];
  partRuns(parts).forEach(run => {
    if (run.role === 'aux' && run.parts.length === 1) { out.push(run.name); return; }
    const counts = {};
    run.parts.forEach(p => {
      const base = labelBase(p.label);
      const n = counts[base] || 0;
      counts[base] = n + 1;
      const suffix = n === 0 ? base : base + n;
      out.push(run.role === 'chorus' ? suffix : `${run.name}_${suffix}`);
    });
  });
  return out;
}

/** {strain, part} anchor → flat part index, or -1 when it dangles. */
function anchorToPi(anchor) {
  if (!anchor || anchor.strain == null) return -1;
  const run = partRuns(S.data.parts).find(r => r.name === anchor.strain);
  if (!run || !(anchor.part >= 0 && anchor.part < run.parts.length)) return -1;
  return run.start + anchor.part;
}

/** Flat part index → {strain, part} anchor (part 0-based within its strain). */
function piToAnchor(pi) {
  const run = partRuns(S.data.parts)
    .find(r => pi >= r.start && pi < r.start + r.parts.length);
  return run ? { strain: run.name, part: pi - run.start } : null;
}

/** <option> list for an anchor picker: one entry per part, by generated id. */
function partOptionsHTML(selectedPi) {
  const ids = partIdList(S.data.parts);
  return ids.map((id, pi) =>
    `<option value="${pi}"${pi === selectedPi ? ' selected' : ''}>${esc(id)}</option>`
  ).join('');
}

// ─── Collect data from DOM → S.data ──────────────────────────────────────────
function collectMeta() {
  // Known fields
  KNOWN_META.forEach(key => {
    const el = qs(`[data-meta="${key}"]`);
    if (!el) return;
    const v = el.value.trim();
    if (v !== '') {
      S.data[key] = key === 'page' ? (parseInt(v, 10) || v) : v;
    } else {
      delete S.data[key];
    }
  });

  // Extra fields (preserve JSON types via JSON.parse round-trip). `strains`,
  // `variants`, `coda_jump` and the editing view have their own editors,
  // so they never appear here.
  const knownSet = new Set([...KNOWN_META, ...DERIVED_META,
                            'strains', 'parts', 'variants', 'coda_jump']);
  const oldExtras = Object.keys(S.data).filter(k => !knownSet.has(k));
  const newExtras = {};
  qsa('[data-extra-key]').forEach(el => {
    const key = el.dataset.extraKey;
    const raw = el.value.trim();
    try { newExtras[key] = JSON.parse(raw); }
    catch { newExtras[key] = raw; }
  });
  oldExtras.forEach(k => delete S.data[k]);
  Object.assign(S.data, newExtras);
}

function collectParts() {
  // Per-card strain identity fields
  qsa('#sections-container .sec-card').forEach(card => {
    const si = +card.dataset.si;
    const part = S.data.parts[si];
    if (!part) return;
    const strain = qs('.part-strain', card)?.value.trim();
    const role   = qs('.part-role', card)?.value;
    const label  = qs('.part-label', card)?.value.trim();
    const plays  = parseInt(qs('.part-plays', card)?.value, 10);
    if (strain) part.strain = strain;
    if (role)   part.role = role;
    part.label = label || part.label;
    part.plays = Number.isFinite(plays) && plays >= 1 ? plays : 1;
  });

  // Reset all beats then repopulate from inputs
  S.data.parts.forEach(p => p.bars.forEach(bar => { bar.beats = {}; }));
  qsa('#sections-container .beat-inp').forEach(el => {
    const si = +el.dataset.si, bi = +el.dataset.bi, beat = el.dataset.beat;
    const v  = normalizeChord(el.value);
    if (v && S.data.parts[si]?.bars[bi]) {
      S.data.parts[si].bars[bi].beats[beat] = v;
    }
  });
}

function collectVariants() {
  if (!Array.isArray(S.data.variants)) return;

  // "Applies to" captions
  qsa('#variants-container .var-applies').forEach(el => {
    const vi = +el.dataset.vi;
    if (!S.data.variants[vi]) return;
    const v = el.value.trim();
    if (v) S.data.variants[vi].applies_to = v;
    else   delete S.data.variants[vi].applies_to;
  });

  // Reset all beats then repopulate from inputs
  S.data.variants.forEach(vr => (vr.bars || []).forEach(bar => { bar.beats = {}; }));
  qsa('#variants-container .beat-inp').forEach(el => {
    const vi = +el.dataset.vi, bi = +el.dataset.bi, beat = el.dataset.beat;
    const v  = normalizeChord(el.value);
    if (v && S.data.variants[vi]?.bars[bi]) {
      S.data.variants[vi].bars[bi].beats[beat] = v;
    }
  });

  // Renumber bars sequentially within each variant
  S.data.variants.forEach(vr => (vr.bars || []).forEach((bar, i) => { bar.bar = i + 1; }));

  // Targets: rebuild each variant's {strain, part, bar} anchors from its rows.
  const collected = S.data.variants.map(() => []);
  qsa('#variants-container .target-row').forEach(row => {
    const vi = +row.dataset.vi;
    if (!collected[vi]) return;
    const pi  = parseInt(qs('.target-sec', row)?.value, 10);
    const bar = parseInt(qs('.target-bar', row)?.value, 10);
    const anchor = Number.isFinite(pi) ? piToAnchor(pi) : null;
    if (anchor && Number.isFinite(bar)) collected[vi].push({ ...anchor, bar });
  });
  S.data.variants.forEach((vr, vi) => {
    if (collected[vi].length) vr.targets = collected[vi];
    else delete vr.targets;
  });
}

function collectFromDOM() {
  collectMeta();
  collectParts();   // parts first: anchor pickers resolve against them
  collectCodaJump();
  collectVariants();
}

// ─── Build save payload ───────────────────────────────────────────────────────
function buildSavePayload() {
  collectFromDOM();

  // A strain name reappearing NON-consecutively would split into duplicate
  // strains (the server rejects that loudly) — warn early.
  const runNames = partRuns(S.data.parts).map(r => r.name);
  const dups = runNames.filter((n, i) => runNames.indexOf(n) !== i);
  if (dups.length) {
    toast(`Warning: strain(s) split by another strain: ${[...new Set(dups)].join(', ')} — reorder the parts`, 'warn');
  }

  // Rebuild object: preserve original key order, strains last. `variants` is
  // kept as-is (already collected/renumbered); an emptied-out list is dropped;
  // legacy derived fields from old WIP files are dropped for good.
  const out = {};
  Object.keys(S.data).forEach(k => {
    if (k === 'strains' || k === 'parts' || S.data[k] === undefined) return;
    if (k === 'variants' && (!Array.isArray(S.data[k]) || !S.data[k].length)) return;
    if (DERIVED_META.includes(k)) return;
    if (k === 'coda_jump' && !S.data[k]?.from?.strain) return; // empty anchor: drop
    out[k] = S.data[k];
  });
  out.strains = partsToStrains(S.data.parts);
  return out;
}

// ─── Render meta ──────────────────────────────────────────────────────────────
function renderMeta() {
  // Known fields grid
  qs('#meta-grid').innerHTML = KNOWN_META.map(key => {
    const val = S.data[key] !== undefined ? String(S.data[key]) : '';
    return `
      <div class="meta-field">
        <label for="meta-${key}">${key.replace(/_/g, ' ')}</label>
        <div class="meta-input-wrap">
          <input id="meta-${key}" type="text" data-meta="${key}"
                 value="${esc(val)}" placeholder="—" />
          <button class="btn-icon danger" data-meta-del="${key}" title="Remove field">×</button>
        </div>
      </div>
    `;
  }).join('');

  // Extra fields (strains/parts, variants and coda_jump have dedicated editors)
  const knownSet  = new Set([...KNOWN_META, ...DERIVED_META,
                             'strains', 'parts', 'variants', 'coda_jump']);
  const extraKeys = Object.keys(S.data).filter(k => !knownSet.has(k));

  if (extraKeys.length) {
    qs('#extra-fields').innerHTML =
      '<div class="extra-section-label">Additional fields</div>' +
      extraKeys.map(key => {
        const val        = S.data[key];
        const displayVal = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
        const isComplex  = typeof val === 'object';
        const autoRows   = isComplex
          ? Math.max(5, Math.min(30, displayVal.split('\n').length + 1))
          : 3;
        const inputHtml  = isComplex
          ? `<textarea class="extra-val-inp" data-extra-key="${esc(key)}" rows="${autoRows}">${esc(displayVal)}</textarea>`
          : `<input  type="text" class="extra-val-inp" data-extra-key="${esc(key)}" value="${esc(displayVal)}" />`;
        return `
          <div class="extra-field-row">
            <span class="extra-key">${esc(key)}</span>
            ${inputHtml}
            <button class="btn-icon danger" data-extra-del="${esc(key)}" title="Remove field">×</button>
          </div>
        `;
      }).join('');
  } else {
    qs('#extra-fields').innerHTML = '';
  }
}

// ─── Coda jump (the printed coda sign anchor) ─────────────────────────────────
// `coda_jump.from = {strain, part, bar}` (part 0-based within its strain, bar
// 1-indexed within that part) is the grid bar carrying the printed coda sign;
// `caption` is the verbatim printed text. The displayers DRAW the sign
// themselves (CSS), so no glyph is stored. Edited here as structured fields,
// mirroring a variant's target anchor (the picker lists generated part ids).
function renderCodaJump() {
  const area = qs('#coda-jump-area');
  if (!area) return;
  const cj = S.data.coda_jump;
  if (!cj) {
    area.innerHTML =
      `<div class="area-label">Coda jump</div>` +
      `<button type="button" id="add-coda-jump" class="btn btn-sm btn-outline">+ Add coda jump</button>`;
    return;
  }
  const from    = cj.from || {};
  const pi      = anchorToPi(from);
  const missing = from.strain != null && pi < 0;
  area.innerHTML = `
    <div class="area-label">Coda jump
      <button type="button" class="btn-icon danger" id="del-coda-jump" title="Remove coda jump">×</button>
    </div>
    <div class="coda-jump-card">
      <div class="coda-field">
        <span class="coda-field-label">caption</span>
        <input id="coda-caption" class="coda-caption-inp" type="text"
               value="${esc(cj.caption || '')}" placeholder="printed text, e.g. CODA Bar 30 :" />
      </div>
      <div class="target-row coda-field${missing ? ' target-missing' : ''}">
        <span class="coda-field-label"
              title="The grid bar carrying the printed coda sign (1-indexed within its part)">from</span>
        <select id="coda-from-sec" class="target-sec" title="Part">
          <option value=""${pi >= 0 ? '' : ' selected'}>—</option>
          ${partOptionsHTML(pi)}
        </select>
        <span class="target-at">bar</span>
        <input id="coda-from-bar" class="target-bar" type="number" min="1" step="1"
               value="${esc(from.bar != null ? from.bar : '')}"
               title="Bar within that part (1-indexed)" />
      </div>
    </div>`;
}

// Reads the coda-jump inputs back into S.data.coda_jump (rebuilt from
// scratch). A no-op when the area shows the +Add button, so a removed
// coda_jump is never resurrected.
function collectCodaJump() {
  if (!qs('#coda-jump-area') || !qs('#coda-caption')) return;
  const caption = qs('#coda-caption').value.trim();
  const pi      = parseInt(qs('#coda-from-sec')?.value, 10);
  const bar     = parseInt(qs('#coda-from-bar')?.value, 10);
  const anchor  = Number.isFinite(pi) ? piToAnchor(pi) : null;
  const cj = {};
  if (caption) cj.caption = caption;
  cj.from = anchor || {};
  if (anchor && Number.isFinite(bar)) cj.from.bar = bar;
  S.data.coda_jump = cj;
}

function addCodaJump() {
  S.data.coda_jump = { caption: '', from: { ...(piToAnchor(0) || {}), bar: 1 } };
  renderCodaJump();
  qs('#coda-caption')?.focus();
  setDirty();
}

function deleteCodaJump() {
  delete S.data.coda_jump;
  renderCodaJump();
  setDirty();
}

// ─── Render parts ─────────────────────────────────────────────────────────────
/* One card per part. The card header edits the part's strain identity
   (strain name + role), its printed label (primes kept) and its plays count
   ("identical parts stored once" — 16 A A stored as one grid with plays 2).
   Consecutive cards sharing a strain name merge into one strain on save. */
function renderSections() {
  const bc        = getBeatCount();
  const total     = S.data.parts.length;
  const container = qs('#sections-container');
  const ids       = partIdList(S.data.parts);

  const strains = [...new Set(['chorus', 'verse',
    ...(S.config.named_strains || []), ...(S.config.aux_connectors || [])])];
  const datalist = `<datalist id="strain-names">
    ${strains.map(n => `<option value="${esc(n)}"></option>`).join('')}
  </datalist>`;

  container.innerHTML = datalist + S.data.parts.map((part, si) =>
    renderPartHTML(part, ids[si], si, total, bc)
  ).join('');

  refreshDuplicateFlags();
  validateAllChords();
}

/** Flag cards whose strain reappears non-consecutively (would split into
    duplicate strains on save) — the strain-model equivalent of the old
    duplicate-section-name warning. */
function refreshDuplicateFlags() {
  const cards = qsa('#sections-container .sec-card');
  const strains = Array.from(cards, c => qs('.part-strain', c)?.value.trim() || '');
  const runNames = [];
  strains.forEach(n => {
    if (!runNames.length || runNames[runNames.length - 1].name !== n) {
      runNames.push({ name: n, count: 0 });
    }
  });
  const runCounts = {};
  runNames.forEach(r => { runCounts[r.name] = (runCounts[r.name] || 0) + 1; });
  cards.forEach((card, i) => {
    const dup = strains[i] !== '' && runCounts[strains[i]] > 1;
    card.classList.toggle('has-dup', dup);
    qs('.sec-dup-badge', card)?.classList.toggle('hidden', !dup);
  });
}

function roleOptionsHTML(selected) {
  return (S.config.roles || []).map(r =>
    `<option value="${esc(r)}"${r === selected ? ' selected' : ''}>${esc(r)}</option>`
  ).join('');
}

function renderPartHTML(part, partId, si, totalParts, bc) {
  const rows    = buildRows(part.bars, 'si', si, bc);
  const canUp   = si > 0;
  const canDown = si < totalParts - 1;

  return `
    <div class="sec-card" data-si="${si}">
      <div class="sec-header">
        <span class="sec-label" title="Generated part id (from strain + label)">${esc(partId)}</span>
        <input class="part-strain" type="text" value="${esc(part.strain)}" data-si="${si}"
               list="strain-names" title="Strain name (chorus, verse, impro, coda, …)" />
        <select class="part-role" data-si="${si}" title="Strain role">
          ${roleOptionsHTML(part.role)}
        </select>
        <span class="part-field-label">label</span>
        <input class="part-label" type="text" value="${esc(part.label)}" data-si="${si}"
               title="Printed label, primes kept (A, A', B, BLUES, Coda)" />
        <span class="part-field-label">×</span>
        <input class="part-plays" type="number" min="1" step="1" value="${esc(part.plays || 1)}"
               data-si="${si}" title="Times played in a row (stored once)" />
        <span class="sec-dup-badge hidden" title="This strain reappears non-consecutively — reorder the parts">⚠ split strain</span>
        <div class="sec-header-btns">
          <button class="btn-icon" data-action="sec-up"   data-si="${si}"
                  ${canUp   ? '' : 'disabled'} title="Move part up">↑</button>
          <button class="btn-icon" data-action="sec-down" data-si="${si}"
                  ${canDown ? '' : 'disabled'} title="Move part down">↓</button>
          <button class="btn-icon" data-action="sec-copy" data-si="${si}"
                  title="Duplicate part">⧉</button>
          <button class="btn-icon danger" data-action="sec-del" data-si="${si}"
                  title="Delete part">×</button>
        </div>
      </div>
      <div class="sec-body">
        ${rows || '<div class="no-bars">No bars — use the buttons below to add some.</div>'}
      </div>
      <div class="sec-footer">
        <button class="btn btn-sm btn-outline" data-action="add-row" data-si="${si}">+ Add Row (4 bars)</button>
        <button class="btn btn-sm btn-outline" data-action="add-bar" data-si="${si}">+ Add Bar</button>
      </div>
    </div>
  `;
}

/* Rows of bars for one section OR one variant. `idxAttr` is the data-attribute
   naming the owning card ('si' for sections, 'vi' for variants); `idx` its
   index. Row/bar actions are shared — the owning container's click handler
   dispatches them against the right array. */
function buildRows(bars, idxAttr, idx, bc) {
  if (!bars.length) return '';
  const totalRows = Math.ceil(bars.length / 4);
  return Array.from({ length: totalRows }, (_, ri) => {
    const slice    = bars.slice(ri * 4, ri * 4 + 4);
    const canUp    = ri > 0;
    const canDown  = ri < totalRows - 1;
    const barCells = slice.map((bar, i) => renderBarHTML(bar, idxAttr, idx, ri * 4 + i, bc)).join('');
    // Pad incomplete last row with empty cells for alignment
    const padding  = '<div class="bar-card bar-empty"></div>'.repeat(4 - slice.length);
    return `
      <div class="bar-row">
        <div class="row-meta">
          <span class="row-label">Row ${ri + 1}</span>
          <button class="btn-icon" data-action="row-up"   data-${idxAttr}="${idx}" data-ri="${ri}"
                  ${canUp   ? '' : 'disabled'} title="Move row up">↑</button>
          <button class="btn-icon" data-action="row-down" data-${idxAttr}="${idx}" data-ri="${ri}"
                  ${canDown ? '' : 'disabled'} title="Move row down">↓</button>
          <button class="btn-icon danger" data-action="row-del"  data-${idxAttr}="${idx}" data-ri="${ri}"
                  title="Delete row">×</button>
        </div>
        <div class="bar-grid">${barCells}${padding}</div>
      </div>
    `;
  }).join('');
}

function renderBarHTML(bar, idxAttr, idx, bi, bc) {
  const beatsHtml = Array.from({ length: bc }, (_, i) => {
    const beat  = String(i + 1);
    const chord = normalizeChord(bar.beats?.[beat] || '');
    return `
      <div class="beat-row">
        <span class="beat-lbl">${beat}</span>
        <input class="beat-inp" type="text"
               data-${idxAttr}="${idx}" data-bi="${bi}" data-beat="${beat}"
               value="${esc(chord)}" placeholder="—" />
      </div>
    `;
  }).join('');

  return `
    <div class="bar-card">
      <div class="bar-header">
        <span class="bar-num">Bar ${bi + 1}</span>
        <button class="btn-icon danger btn-xs" data-action="bar-del"
                data-${idxAttr}="${idx}" data-bi="${bi}" title="Delete bar">×</button>
      </div>
      <div class="bar-beats">${beatsHtml}</div>
    </div>
  `;
}

// ─── Structural operations ────────────────────────────────────────────────────
function moveSectionUp(si) {
  const s = S.data.parts;
  [s[si - 1], s[si]] = [s[si], s[si - 1]];
  renderSections();
}

function moveSectionDown(si) {
  moveSectionUp(si + 1);
}

function copySection(si) {
  const src   = S.data.parts[si];
  const clone = {
    strain: src.strain,
    role:   src.role,
    label:  src.label,
    plays:  1,
    bars: src.bars.map(bar => ({
      bar:   bar.bar,
      beats: Object.assign({}, bar.beats),
    })),
  };
  S.data.parts.splice(si + 1, 0, clone);
  renderSections();
}

function deleteSection(si) {
  const part = S.data.parts[si];
  const name = part ? `${part.strain} ${part.label}` : si;
  if (!confirm(`Delete part "${name}" and all its bars?`)) return;
  S.data.parts.splice(si, 1);
  renderSections();
}

function moveRowUp(si, ri) {
  const bars  = S.data.parts[si].bars;
  const pStart = (ri - 1) * 4;
  const tStart = ri * 4;
  const prev   = bars.slice(pStart, tStart);
  const curr   = bars.slice(tStart, tStart + 4);
  S.data.parts[si].bars = [
    ...bars.slice(0, pStart),
    ...curr,
    ...prev,
    ...bars.slice(tStart + 4),
  ];
  renderSections();
}

function moveRowDown(si, ri) {
  moveRowUp(si, ri + 1);
}

function deleteRow(si, ri) {
  const bars = S.data.parts[si].bars;
  S.data.parts[si].bars = [
    ...bars.slice(0, ri * 4),
    ...bars.slice(ri * 4 + 4),
  ];
  renderSections();
}

function deleteBar(si, bi) {
  S.data.parts[si].bars.splice(bi, 1);
  renderSections();
}

function addRow(si) {
  const empty = Array.from({ length: 4 }, () => ({ bar: 0, beats: {} }));
  S.data.parts[si].bars.push(...empty);
  renderSections();
  setDirty();
}

function addBar(si) {
  S.data.parts[si].bars.push({ bar: 0, beats: {} });
  renderSections();
  setDirty();
}

/** Next unused chorus letter, for a sensible new-part default. */
function nextChorusLabel() {
  const used = new Set(S.data.parts.filter(p => p.role === 'chorus')
    .map(p => labelBase(p.label)));
  for (const ch of 'ABCDEFGH') if (!used.has(ch)) return ch;
  return 'A';
}

function addSection() {
  collectFromDOM();
  const last = S.data.parts[S.data.parts.length - 1];
  S.data.parts.push({
    strain: last ? last.strain : 'chorus',
    role:   last ? last.role : 'chorus',
    label:  nextChorusLabel(),
    plays:  1,
    bars:   Array.from({ length: 4 }, () => ({ bar: 0, beats: {} })),
  });
  renderSections();
  setDirty();
  // Scroll to new part
  setTimeout(() => {
    const cards = qsa('#sections-container .sec-card');
    cards[cards.length - 1]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 50);
}

// ─── Variant targets (structured strain+part+bar anchors) ─────────────────────
/* A variant's `targets` map it onto the grid: one {strain, part, bar} per
   occurrence, pointing at the grid bar (1-indexed within its part) where the
   variant's FIRST bar lands; the rest follow consecutively in the same part.
   The free-text `applies_to` caption is kept for display only. */

// Strains that don't count toward the "chorus" frame the captions use.
const AUX_FRAME_RE = /^(intro|interlude|transition)$/;

/** Chorus bars in printed order as {pi, bar} (1-indexed), auxiliary parts
    skipped — the frame a legacy `applies_to` caption's numbers count over. */
function chorusFlat() {
  const flat = [];
  (S.data.parts || []).forEach((part, pi) => {
    if (part.role === 'verse' || part.role === 'aux'
        || AUX_FRAME_RE.test(part.strain)) return;
    (part.bars || []).forEach((_bar, i) => flat.push({ pi, bar: i + 1 }));
  });
  return flat;
}

/** Derive `targets` from a variant's caption over the chorus frame (same rule as
    the backfill): one anchor per caption number whose run stays in one part. */
function deriveTargets(variant) {
  const starts = (String(variant.applies_to || '').match(/\d+/g) || []).map(Number);
  const nb = (variant.bars || []).length;
  const flat = chorusFlat();
  const out = [];
  if (!nb) return out;
  starts.forEach(s => {
    if (s < 1 || s + nb - 1 > flat.length) return;   // out of range
    const run = flat.slice(s - 1, s - 1 + nb);
    if (new Set(run.map(r => r.pi)).size > 1) return; // straddles parts
    out.push({ ...piToAnchor(run[0].pi), bar: run[0].bar });
  });
  return out;
}

// ─── Render variants ──────────────────────────────────────────────────────────
/* Variants are alternative changes for certain bars. They edit exactly like
   sections — same bar/row/beat grid and chord validation — but carry a free-text
   "applies to" caption plus a structured `targets` list instead of a section name. */
function renderVariants() {
  const bc        = getBeatCount();
  const variants  = Array.isArray(S.data.variants) ? S.data.variants : [];
  const container = qs('#variants-container');

  container.innerHTML = variants.map((v, vi) =>
    renderVariantHTML(v, vi, variants.length, bc)
  ).join('');

  validateAllChords();
}

function renderVariantHTML(variant, vi, totalVariants, bc) {
  const rows    = buildRows(variant.bars || [], 'vi', vi, bc);
  const canUp   = vi > 0;
  const canDown = vi < totalVariants - 1;

  return `
    <div class="sec-card variant-card" data-vi="${vi}">
      <div class="sec-header">
        <span class="sec-label">Variant</span>
        <input class="var-applies" type="text" value="${esc(variant.applies_to || '')}" data-vi="${vi}"
               placeholder="applies to… (e.g. Bars 2, 10, 26)" title="Applies to" />
        <div class="sec-header-btns">
          <button class="btn-icon" data-action="var-up"   data-vi="${vi}"
                  ${canUp   ? '' : 'disabled'} title="Move variant up">↑</button>
          <button class="btn-icon" data-action="var-down" data-vi="${vi}"
                  ${canDown ? '' : 'disabled'} title="Move variant down">↓</button>
          <button class="btn-icon" data-action="var-copy" data-vi="${vi}"
                  title="Duplicate variant">⧉</button>
          <button class="btn-icon danger" data-action="var-del" data-vi="${vi}"
                  title="Delete variant">×</button>
        </div>
      </div>
      ${renderVariantTargetsHTML(variant, vi)}
      <div class="sec-body">
        ${rows || '<div class="no-bars">No bars — use the buttons below to add some.</div>'}
      </div>
      <div class="sec-footer">
        <button class="btn btn-sm btn-outline" data-action="add-row" data-vi="${vi}">+ Add Row (4 bars)</button>
        <button class="btn btn-sm btn-outline" data-action="add-bar" data-vi="${vi}">+ Add Bar</button>
      </div>
    </div>
  `;
}

/* The targets editor: one row per {strain, part, bar} anchor. The part
   dropdown lists the generated part ids; a stored-but-dangling anchor is
   flagged (never silently dropped — fix it or remove the row). "Auto from
   caption" fills the rows from `applies_to` over the chorus frame. */
function renderVariantTargetsHTML(variant, vi) {
  const targets = Array.isArray(variant.targets) ? variant.targets : [];

  const rows = targets.map((tg, ti) => {
    const pi = anchorToPi(tg);
    const missing = tg.strain != null && pi < 0;
    return `
      <div class="target-row${missing ? ' target-missing' : ''}" data-vi="${vi}" data-ti="${ti}">
        <select class="target-sec" data-vi="${vi}" data-ti="${ti}"
                title="Part${missing ? ` — stored anchor ${esc(tg.strain)}[${esc(tg.part)}] no longer resolves` : ''}">
          <option value=""${pi >= 0 ? '' : ' selected'}>${missing ? `⚠ ${esc(tg.strain)}[${esc(tg.part)}]` : '—'}</option>
          ${partOptionsHTML(pi)}
        </select>
        <span class="target-at">bar</span>
        <input class="target-bar" type="number" min="1" step="1"
               data-vi="${vi}" data-ti="${ti}"
               value="${esc(tg.bar != null ? tg.bar : '')}"
               title="Bar within that part (1-indexed) where the variant's first bar lands" />
        <button class="btn-icon danger btn-xs" data-action="target-del"
                data-vi="${vi}" data-ti="${ti}" title="Remove target">×</button>
      </div>`;
  }).join('');

  return `
    <div class="var-targets" data-vi="${vi}">
      <div class="var-targets-head">
        <span class="var-targets-label"
              title="Grid bars this variant swaps into — one per occurrence; the first variant bar lands here, the rest follow within the same section">Targets</span>
        <button class="btn btn-xs btn-outline" data-action="target-auto" data-vi="${vi}"
                title="Fill targets from the caption, counting the chorus (verse/intro skipped)">Auto from caption</button>
        <button class="btn btn-xs btn-outline" data-action="target-add" data-vi="${vi}">+ Target</button>
      </div>
      <div class="var-targets-rows">
        ${rows || '<div class="no-targets">No targets — use “Auto from caption” or “+ Target”.</div>'}
      </div>
    </div>`;
}

// ─── Variant structural operations ─────────────────────────────────────────────
function addVariantTarget(vi) {
  const v = S.data.variants[vi];
  if (!v) return;
  if (!Array.isArray(v.targets)) v.targets = [];
  v.targets.push({ ...(piToAnchor(0) || {}), bar: 1 });
  renderVariants();
  setDirty();
}

function deleteVariantTarget(vi, ti) {
  const v = S.data.variants[vi];
  if (!v || !Array.isArray(v.targets)) return;
  v.targets.splice(ti, 1);
  if (!v.targets.length) delete v.targets;
  renderVariants();
}

function autoVariantTargets(vi) {
  const v = S.data.variants[vi];
  if (!v) return;
  const derived = deriveTargets(v);
  if (!derived.length) {
    toast('Could not derive targets — check “applies to” and the section lengths.', 'warn');
    return;
  }
  v.targets = derived;
  renderVariants();
  setDirty();
}

function moveVariantUp(vi) {
  const v = S.data.variants;
  [v[vi - 1], v[vi]] = [v[vi], v[vi - 1]];
  renderVariants();
}

function moveVariantDown(vi) {
  moveVariantUp(vi + 1);
}

function copyVariant(vi) {
  const src   = S.data.variants[vi];
  const clone = {
    ...(src.applies_to ? { applies_to: src.applies_to } : {}),
    ...(Array.isArray(src.targets) && src.targets.length
      ? { targets: src.targets.map(t => ({ ...t })) }
      : {}),
    bars: (src.bars || []).map(bar => ({
      bar:   bar.bar,
      beats: Object.assign({}, bar.beats),
    })),
  };
  S.data.variants.splice(vi + 1, 0, clone);
  renderVariants();
}

function deleteVariant(vi) {
  if (!confirm('Delete this variant and all its bars?')) return;
  S.data.variants.splice(vi, 1);
  renderVariants();
}

function moveVariantRowUp(vi, ri) {
  const bars   = S.data.variants[vi].bars;
  const pStart = (ri - 1) * 4;
  const tStart = ri * 4;
  const prev   = bars.slice(pStart, tStart);
  const curr   = bars.slice(tStart, tStart + 4);
  S.data.variants[vi].bars = [
    ...bars.slice(0, pStart),
    ...curr,
    ...prev,
    ...bars.slice(tStart + 4),
  ];
  renderVariants();
}

function moveVariantRowDown(vi, ri) {
  moveVariantRowUp(vi, ri + 1);
}

function deleteVariantRow(vi, ri) {
  const bars = S.data.variants[vi].bars;
  S.data.variants[vi].bars = [
    ...bars.slice(0, ri * 4),
    ...bars.slice(ri * 4 + 4),
  ];
  renderVariants();
}

function deleteVariantBar(vi, bi) {
  S.data.variants[vi].bars.splice(bi, 1);
  renderVariants();
}

function addVariantRow(vi) {
  const empty = Array.from({ length: 4 }, () => ({ bar: 0, beats: {} }));
  S.data.variants[vi].bars.push(...empty);
  renderVariants();
  setDirty();
}

function addVariantBar(vi) {
  S.data.variants[vi].bars.push({ bar: 0, beats: {} });
  renderVariants();
  setDirty();
}

function addVariant() {
  collectFromDOM();
  if (!Array.isArray(S.data.variants)) S.data.variants = [];
  S.data.variants.push({
    applies_to: '',
    bars: Array.from({ length: 4 }, () => ({ bar: 0, beats: {} })),
  });
  renderVariants();
  setDirty();
  // Scroll to new variant
  setTimeout(() => {
    const cards = qsa('#variants-container .variant-card');
    cards[cards.length - 1]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 50);
}

// ─── Save & Verify ────────────────────────────────────────────────────────────
async function doSave() {
  if (!S.currentId) return;
  try {
    const payload = buildSavePayload();
    await apiFetch(`/api/tunes/${encodeURIComponent(S.currentId)}`, {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    clearDirty();
    const bad  = invalidChordList(payload);
    const badT = invalidTargetList(payload);
    if (bad.length || badT.length) {
      const parts = [];
      if (bad.length)  parts.push(`${bad.length} chord${bad.length > 1 ? 's' : ''} look wrong`);
      if (badT.length) parts.push(`${badT.length} variant target${badT.length > 1 ? 's' : ''} off`);
      const detail = [...bad.slice(0, 2), ...badT.slice(0, 2)].join(' · ');
      toast(`Saved ✓ — but ${parts.join(', ')}: ${detail}${(bad.length + badT.length) > 2 ? ' …' : ''}`, 'warn');
    } else {
      toast('Saved ✓', 'success');
    }
    // Refresh title in case it was changed
    qs('#editor-title').textContent = payload.title || S.currentId;
    // Update title in sidebar list
    const t = S.tunes.find(t => t.id === S.currentId);
    if (t) { t.title = payload.title || t.title; renderSidebar(); }
  } catch (err) {
    toast(`Save failed: ${err.message}`, 'error');
  }
}

async function doVerify() {
  if (!S.currentId) return;
  // Auto-save if dirty
  if (S.dirty) {
    await doSave();
    if (S.dirty) return; // save failed
  }
  try {
    await apiFetch(`/api/tunes/${encodeURIComponent(S.currentId)}/verify`, { method: 'POST' });
    const t = S.tunes.find(t => t.id === S.currentId);
    if (t) { t.verified = true; t.deferred = false; }
    afterStatusChange();
    toast('Marked as verified ✓', 'success');
  } catch (err) {
    toast(`Verify failed: ${err.message}`, 'error');
  }
}

async function doUnverify() {
  if (!S.currentId) return;
  if (!confirm('Remove the verified mark? The file in tunes_verified/ will be deleted.')) return;
  try {
    await apiFetch(`/api/tunes/${encodeURIComponent(S.currentId)}/verify`, { method: 'DELETE' });
    const t = S.tunes.find(t => t.id === S.currentId);
    if (t) t.verified = false;
    afterStatusChange();
    toast('Verification removed', 'info');
  } catch (err) {
    toast(`Failed: ${err.message}`, 'error');
  }
}

// ─── Defer / Resume ───────────────────────────────────────────────────────────
/* Deferring parks a tune for a later review pass: it drops out of the
   "needs review" queue into the "deferred" tab until resumed. Any pending
   edits are saved first so nothing is lost. */
async function doDefer() {
  if (!S.currentId) return;
  if (S.dirty) {
    await doSave();
    if (S.dirty) return; // save failed
  }
  try {
    await apiFetch(`/api/tunes/${encodeURIComponent(S.currentId)}/defer`, { method: 'POST' });
    const t = S.tunes.find(t => t.id === S.currentId);
    if (t) { t.deferred = true; t.verified = false; }
    afterStatusChange();
    toast('Deferred for later ⏸', 'info');
  } catch (err) {
    toast(`Defer failed: ${err.message}`, 'error');
  }
}

async function doResume() {
  if (!S.currentId) return;
  try {
    await apiFetch(`/api/tunes/${encodeURIComponent(S.currentId)}/defer`, { method: 'DELETE' });
    const t = S.tunes.find(t => t.id === S.currentId);
    if (t) t.deferred = false;
    afterStatusChange();
    toast('Back in the review queue', 'info');
  } catch (err) {
    toast(`Failed: ${err.message}`, 'error');
  }
}

// ─── Action buttons state ─────────────────────────────────────────────────────
function updateActionButtons() {
  const t        = S.tunes.find(t => t.id === S.currentId);
  const verified = t?.verified ?? false;
  const deferred = t?.deferred ?? false;
  qs('#btn-verify').classList.toggle('hidden', verified);
  qs('#btn-unverify').classList.toggle('hidden', !verified);
  qs('#btn-defer').classList.toggle('hidden', verified || deferred);
  qs('#btn-resume').classList.toggle('hidden', !deferred);
}

// ─── Open a tune ──────────────────────────────────────────────────────────────
async function openTune(id) {
  if (S.currentId === id) return;

  // Guard unsaved changes
  if (S.dirty) {
    if (!confirm('You have unsaved changes. Discard and open another tune?')) return;
  }

  try {
    const result = await apiFetch(`/api/tunes/${encodeURIComponent(id)}`);
    const raw    = result.data;

    S.currentId = id;
    // The server always sends the strains model (raw sources are converted
    // on load); the editor works on the flat per-part view.
    S.data      = { ...raw, parts: strainsToParts(raw.strains) };
    delete S.data.strains;
    S.dirty     = false;

    // Sync review state from server response
    const t = S.tunes.find(t => t.id === id);
    if (t) {
      t.verified = result.verified;
      t.deferred = result.deferred;
    }

    renderEditor();
    renderSidebar(); // update active highlight
    apiPutState({ last_opened: id });
  } catch (err) {
    toast(`Failed to load "${id}": ${err.message}`, 'error');
  }
}

// ─── Full editor render ───────────────────────────────────────────────────────
function renderEditor() {
  qs('#no-tune').classList.add('hidden');
  qs('#editor-wrap').classList.remove('hidden');

  qs('#editor-title').textContent = S.data?.title || S.currentId;

  renderMeta();
  renderSections();
  renderCodaJump();
  renderVariants();
  updateActionButtons();
  clearDirty();

  // Image panel
  const tune = S.tunes.find(t => t.id === S.currentId);
  if (tune?.has_image) {
    qs('#crop-img').src = `/crop/${encodeURIComponent(S.currentId)}`;
    qs('#crop-filename').textContent = `${S.currentId}.png`;
    qs('#image-panel').classList.remove('hidden');
  } else {
    qs('#image-panel').classList.add('hidden');
  }

  // Scroll editor to top
  qs('#editor-scroll').scrollTop = 0;
}

// ─── Event wiring ─────────────────────────────────────────────────────────────
function wireEvents() {

  // ── Sidebar tune selection
  qs('#tune-list').addEventListener('click', e => {
    const item = e.target.closest('.tune-item');
    if (item) openTune(item.dataset.id);
  });

  // ── Filter tabs
  qs('#filter-tabs').addEventListener('click', e => {
    const btn = e.target.closest('button[data-filter]');
    if (!btn) return;
    setFilter(btn.dataset.filter);
    renderSidebar();
    // If the open tune isn't in this view, jump to the first one that is.
    const vis = visibleTunes();
    if (vis.length && !vis.some(t => t.id === S.currentId)) openTune(vis[0].id);
  });

  // ── Save / Verify / Defer buttons
  qs('#btn-save').addEventListener('click',     () => doSave());
  qs('#btn-verify').addEventListener('click',   () => doVerify());
  qs('#btn-unverify').addEventListener('click', () => doUnverify());
  qs('#btn-defer').addEventListener('click',    () => doDefer());
  qs('#btn-resume').addEventListener('click',   () => doResume());
  qs('#btn-add-section').addEventListener('click', () => addSection());
  qs('#btn-add-variant').addEventListener('click', () => addVariant());

  // ── Ctrl+S
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      if (S.currentId) doSave();
    }
    if (e.key === 'Escape') {
      qs('#zoom-overlay').classList.add('hidden');
    }
  });

  // ── Meta: dirty on input
  qs('#meta-grid').addEventListener('input', () => setDirty());
  qs('#extra-fields').addEventListener('input', () => setDirty());

  // ── Coda jump: dirty on input/change, add/remove buttons
  qs('#coda-jump-area').addEventListener('input',  () => setDirty());
  qs('#coda-jump-area').addEventListener('change', () => setDirty());
  qs('#coda-jump-area').addEventListener('click', e => {
    if (e.target.closest('#add-coda-jump'))      { collectFromDOM(); addCodaJump(); }
    else if (e.target.closest('#del-coda-jump')) { deleteCodaJump(); }
  });

  // ── Meta: delete field button
  qs('#meta-grid').addEventListener('click', e => {
    const btn = e.target.closest('[data-meta-del]');
    if (!btn) return;
    collectMeta();
    delete S.data[btn.dataset.metaDel];
    renderMeta();
    setDirty();
  });

  qs('#extra-fields').addEventListener('click', e => {
    const btn = e.target.closest('[data-extra-del]');
    if (!btn) return;
    collectMeta(); // save current extra values first
    delete S.data[btn.dataset.extraDel];
    renderMeta();
    setDirty();
  });

  // ── Add arbitrary meta field
  qs('#btn-add-field').addEventListener('click', () => {
    const key = prompt('Field name (e.g. "key", "notes"):');
    if (!key?.trim()) return;
    const val = prompt(`Value for "${key.trim()}":`, '');
    if (val === null) return;
    collectMeta();
    S.data[key.trim()] = val;
    renderMeta();
    setDirty();
  });

  // ── time_signature change → re-render beat slots
  qs('#meta-grid').addEventListener('change', e => {
    if (e.target.dataset.meta === 'time_signature') {
      collectFromDOM();
      renderSections();
      renderVariants();
    }
  });

  // ── Sections: structural actions (delegated)
  qs('#sections-container').addEventListener('click', e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;

    const action = btn.dataset.action;
    const si = btn.dataset.si != null ? +btn.dataset.si : undefined;
    const ri = btn.dataset.ri != null ? +btn.dataset.ri : undefined;
    const bi = btn.dataset.bi != null ? +btn.dataset.bi : undefined;

    // Persist current input values before mutating
    collectFromDOM();
    setDirty();

    switch (action) {
      case 'sec-up':   moveSectionUp(si);    break;
      case 'sec-down': moveSectionDown(si);  break;
      case 'sec-copy': copySection(si);      break;
      case 'sec-del':  deleteSection(si);    break;
      case 'row-up':   moveRowUp(si, ri);    break;
      case 'row-down': moveRowDown(si, ri);  break;
      case 'row-del':  deleteRow(si, ri);    break;
      case 'bar-del':  deleteBar(si, bi);    break;
      case 'add-row':  addRow(si);           break;
      case 'add-bar':  addBar(si);           break;
    }
  });

  // ── Parts: dirty on any input; re-flag split strains when a strain name
  //    changes; live-check chord syntax while typing. The generated part id
  //    in a card header refreshes on the next structural re-render.
  qs('#sections-container').addEventListener('input', e => {
    setDirty();
    if (e.target.classList.contains('part-strain')) refreshDuplicateFlags();
    if (e.target.classList.contains('beat-inp')) validateChordInput(e.target, true);
  });

  // Role selects fire 'change', not 'input'; a role change can affect the
  // generated ids and the run grouping, so re-render the cards.
  qs('#sections-container').addEventListener('change', e => {
    if (e.target.classList.contains('part-role')
        || e.target.classList.contains('part-strain')
        || e.target.classList.contains('part-label')
        || e.target.classList.contains('part-plays')) {
      setDirty();
      collectFromDOM();
      renderSections();
      renderCodaJump();
      renderVariants();
    }
  });

  // ── Variants: structural actions (delegated). Bar/row/add actions are shared
  //    with sections; here they dispatch against S.data.variants.
  qs('#variants-container').addEventListener('click', e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;

    const action = btn.dataset.action;
    const vi = btn.dataset.vi != null ? +btn.dataset.vi : undefined;
    const ri = btn.dataset.ri != null ? +btn.dataset.ri : undefined;
    const bi = btn.dataset.bi != null ? +btn.dataset.bi : undefined;
    const ti = btn.dataset.ti != null ? +btn.dataset.ti : undefined;

    // Persist current input values before mutating
    collectFromDOM();
    setDirty();

    switch (action) {
      case 'var-up':      moveVariantUp(vi);         break;
      case 'var-down':    moveVariantDown(vi);       break;
      case 'var-copy':    copyVariant(vi);           break;
      case 'var-del':     deleteVariant(vi);         break;
      case 'row-up':      moveVariantRowUp(vi, ri);  break;
      case 'row-down':    moveVariantRowDown(vi, ri); break;
      case 'row-del':     deleteVariantRow(vi, ri);  break;
      case 'bar-del':     deleteVariantBar(vi, bi);  break;
      case 'add-row':     addVariantRow(vi);         break;
      case 'add-bar':     addVariantBar(vi);         break;
      case 'target-add':  addVariantTarget(vi);      break;
      case 'target-del':  deleteVariantTarget(vi, ti); break;
      case 'target-auto': autoVariantTargets(vi);    break;
    }
  });

  // ── Variants: dirty on any input; live-check chord syntax while typing
  qs('#variants-container').addEventListener('input', e => {
    setDirty();
    if (e.target.classList.contains('beat-inp')) validateChordInput(e.target, true);
  });

  // ── Variants: target section dropdowns fire 'change', not 'input'
  qs('#variants-container').addEventListener('change', e => {
    if (e.target.classList.contains('target-sec')) setDirty();
  });

  // ── Chord hint bubble follows focus
  qs('#sections-container').addEventListener('focusin', e => {
    if (e.target.classList.contains('beat-inp')) validateChordInput(e.target, true);
  });
  qs('#sections-container').addEventListener('focusout', e => {
    if (e.target.classList.contains('beat-inp')) { normalizeBeatInput(e.target); hideChordHint(); }
  });
  qs('#variants-container').addEventListener('focusin', e => {
    if (e.target.classList.contains('beat-inp')) validateChordInput(e.target, true);
  });
  qs('#variants-container').addEventListener('focusout', e => {
    if (e.target.classList.contains('beat-inp')) { normalizeBeatInput(e.target); hideChordHint(); }
  });
  qs('#editor-scroll').addEventListener('scroll', hideChordHint);

  // ── Image zoom
  qs('#crop-img').addEventListener('click', openZoom);
  qs('#btn-zoom').addEventListener('click', openZoom);
  qs('#zoom-close').addEventListener('click', () => {
    qs('#zoom-overlay').classList.add('hidden');
  });
  qs('#zoom-overlay').addEventListener('click', e => {
    if (e.target === qs('#zoom-overlay') || e.target === qs('#zoom-scroll')) {
      qs('#zoom-overlay').classList.add('hidden');
    }
  });
}

function openZoom() {
  const src = qs('#crop-img').src;
  if (!src) return;
  qs('#zoom-img').src = src;
  qs('#zoom-overlay').classList.remove('hidden');
}

// ─── Initialisation ───────────────────────────────────────────────────────────
async function init() {
  wireEvents();
  try {
    const result = await apiFetch('/api/tunes');
    S.tunes = result.tunes;
    if (result.config) S.config = result.config;
    // Start on the "needs review" queue, but fall through to "all" once it's
    // empty so the list is never blank on first load.
    if (!visibleTunes().length) setFilter('all');
    renderSidebar();

    const startId = result.last_opened || visibleTunes()[0]?.id || S.tunes[0]?.id;
    if (startId) await openTune(startId);
    else qs('#no-tune').textContent = 'No tune files found in tunes/.';
  } catch (err) {
    console.error('Init failed:', err);
    toast('Failed to load tune list', 'error');
  }
}

init();
