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
  tunes:     [],    // [{id, title, verified, has_image}]
  currentId: null,  // ID of open tune
  data:      null,  // tune data; sections is [{name, bars}] (array form)
  dirty:     false,
};

// Known meta fields shown in the grid (in display order)
const KNOWN_META = [
  'title', 'composer', 'year', 'style',
  'tempo', 'form', 'time_signature', 'page', 'source',
];

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
  const total = S.tunes.length;
  const done  = S.tunes.filter(t => t.verified).length;
  qs('#progress-text').textContent = `${done} / ${total} verified`;
  qs('#progress-fill').style.width = total > 0 ? `${(done / total) * 100}%` : '0%';
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────
function renderSidebar() {
  qs('#tune-list').innerHTML = S.tunes.map(t => `
    <li class="tune-item${t.verified ? ' verified' : ''}${t.id === S.currentId ? ' active' : ''}"
        data-id="${esc(t.id)}">
      <span class="tune-status">${t.verified ? '✓' : '○'}</span>
      <span class="tune-title">${esc(t.title || t.id)}</span>
    </li>
  `).join('');
  updateProgress();
}

// ─── Data conversion ──────────────────────────────────────────────────────────
/** Convert sections object {A:[bars]} → array [{name,bars}] */
function sectionsToArray(obj) {
  return Object.entries(obj || {}).map(([name, bars]) => ({
    name,
    bars: (bars || []).map(b => ({
      bar:   b.bar,
      beats: Object.assign({}, b.beats || {}),
    })),
  }));
}

/** Convert sections array [{name,bars}] → object {A:[bars]}, bars renumbered */
function sectionsToObject(arr) {
  const obj = {};
  (arr || []).forEach(sec => {
    obj[sec.name] = sec.bars.map((bar, i) => ({
      bar:   i + 1,
      beats: bar.beats,
    }));
  });
  return obj;
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

  // Extra fields (preserve JSON types via JSON.parse round-trip)
  const knownSet = new Set([...KNOWN_META, 'sections']);
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

function collectSections() {
  // Section names
  qsa('.sec-name').forEach(el => {
    const si = +el.dataset.si;
    if (S.data.sections[si]) S.data.sections[si].name = el.value.trim() || S.data.sections[si].name;
  });

  // Reset all beats then repopulate from inputs
  S.data.sections.forEach(sec => sec.bars.forEach(bar => { bar.beats = {}; }));
  qsa('.beat-inp').forEach(el => {
    const si = +el.dataset.si, bi = +el.dataset.bi, beat = el.dataset.beat;
    const v  = el.value.trim();
    if (v && S.data.sections[si]?.bars[bi]) {
      S.data.sections[si].bars[bi].beats[beat] = v;
    }
  });
}

function collectFromDOM() {
  collectMeta();
  collectSections();
}

// ─── Build save payload ───────────────────────────────────────────────────────
function buildSavePayload() {
  collectFromDOM();

  // Check duplicate section names (warn, but proceed)
  const names = S.data.sections.map(s => s.name);
  const dups  = names.filter((n, i) => names.indexOf(n) !== i);
  if (dups.length) toast(`Warning: duplicate section names: ${dups.join(', ')}`, 'warn');

  // Rebuild object: preserve original key order, sections last
  const out = {};
  Object.keys(S.data).forEach(k => {
    if (k !== 'sections' && S.data[k] !== undefined) out[k] = S.data[k];
  });
  out.sections = sectionsToObject(S.data.sections);
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

  // Extra fields
  const knownSet  = new Set([...KNOWN_META, 'sections']);
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

// ─── Render sections ─────────────────────────────────────────────────────────
function renderSections() {
  const bc        = getBeatCount();
  const total     = S.data.sections.length;
  const container = qs('#sections-container');

  container.innerHTML = S.data.sections.map((sec, si) =>
    renderSectionHTML(sec, si, total, bc)
  ).join('');

  refreshDuplicateFlags();
}

/** Flag section cards whose (live) name collides with another section's. */
function refreshDuplicateFlags() {
  const cards  = qsa('.sec-card');
  const names  = Array.from(cards, c => qs('.sec-name', c).value.trim());
  const counts = {};
  names.forEach(n => { if (n) counts[n] = (counts[n] || 0) + 1; });

  cards.forEach((card, i) => {
    const dup = names[i] !== '' && counts[names[i]] > 1;
    card.classList.toggle('has-dup', dup);
    qs('.sec-dup-badge', card)?.classList.toggle('hidden', !dup);
  });
}

function renderSectionHTML(sec, si, totalSections, bc) {
  const rows    = buildRows(sec.bars, si, bc);
  const canUp   = si > 0;
  const canDown = si < totalSections - 1;

  return `
    <div class="sec-card" data-si="${si}">
      <div class="sec-header">
        <span class="sec-label">Section</span>
        <input class="sec-name" type="text" value="${esc(sec.name)}" data-si="${si}"
               title="Section name" />
        <span class="sec-dup-badge hidden" title="Another section has this name — they will be merged on save">⚠ duplicate name</span>
        <div class="sec-header-btns">
          <button class="btn-icon" data-action="sec-up"   data-si="${si}"
                  ${canUp   ? '' : 'disabled'} title="Move section up">↑</button>
          <button class="btn-icon" data-action="sec-down" data-si="${si}"
                  ${canDown ? '' : 'disabled'} title="Move section down">↓</button>
          <button class="btn-icon" data-action="sec-copy" data-si="${si}"
                  title="Duplicate section">⧉</button>
          <button class="btn-icon danger" data-action="sec-del" data-si="${si}"
                  title="Delete section">×</button>
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

function buildRows(bars, si, bc) {
  if (!bars.length) return '';
  const totalRows = Math.ceil(bars.length / 4);
  return Array.from({ length: totalRows }, (_, ri) => {
    const slice    = bars.slice(ri * 4, ri * 4 + 4);
    const canUp    = ri > 0;
    const canDown  = ri < totalRows - 1;
    const barCells = slice.map((bar, i) => renderBarHTML(bar, si, ri * 4 + i, bc)).join('');
    // Pad incomplete last row with empty cells for alignment
    const padding  = '<div class="bar-card bar-empty"></div>'.repeat(4 - slice.length);
    return `
      <div class="bar-row">
        <div class="row-meta">
          <span class="row-label">Row ${ri + 1}</span>
          <button class="btn-icon" data-action="row-up"   data-si="${si}" data-ri="${ri}"
                  ${canUp   ? '' : 'disabled'} title="Move row up">↑</button>
          <button class="btn-icon" data-action="row-down" data-si="${si}" data-ri="${ri}"
                  ${canDown ? '' : 'disabled'} title="Move row down">↓</button>
          <button class="btn-icon danger" data-action="row-del"  data-si="${si}" data-ri="${ri}"
                  title="Delete row">×</button>
        </div>
        <div class="bar-grid">${barCells}${padding}</div>
      </div>
    `;
  }).join('');
}

function renderBarHTML(bar, si, bi, bc) {
  const beatsHtml = Array.from({ length: bc }, (_, i) => {
    const beat  = String(i + 1);
    const chord = bar.beats?.[beat] || '';
    return `
      <div class="beat-row">
        <span class="beat-lbl">${beat}</span>
        <input class="beat-inp" type="text"
               data-si="${si}" data-bi="${bi}" data-beat="${beat}"
               value="${esc(chord)}" placeholder="—" />
      </div>
    `;
  }).join('');

  return `
    <div class="bar-card">
      <div class="bar-header">
        <span class="bar-num">Bar ${bi + 1}</span>
        <button class="btn-icon danger btn-xs" data-action="bar-del"
                data-si="${si}" data-bi="${bi}" title="Delete bar">×</button>
      </div>
      <div class="bar-beats">${beatsHtml}</div>
    </div>
  `;
}

// ─── Structural operations ────────────────────────────────────────────────────
function moveSectionUp(si) {
  const s = S.data.sections;
  [s[si - 1], s[si]] = [s[si], s[si - 1]];
  renderSections();
}

function moveSectionDown(si) {
  moveSectionUp(si + 1);
}

/** Derive a section name not already used, so copies don't collide on save. */
function uniqueSectionName(base) {
  const existing = new Set(S.data.sections.map(s => s.name));
  if (!existing.has(base)) return base;
  let i = 2;
  while (existing.has(`${base} (${i})`)) i++;
  return `${base} (${i})`;
}

function copySection(si) {
  const src   = S.data.sections[si];
  const clone = {
    name: uniqueSectionName(src.name),
    bars: src.bars.map(bar => ({
      bar:   bar.bar,
      beats: Object.assign({}, bar.beats),
    })),
  };
  S.data.sections.splice(si + 1, 0, clone);
  renderSections();
}

function deleteSection(si) {
  const name = S.data.sections[si]?.name || si;
  if (!confirm(`Delete section "${name}" and all its bars?`)) return;
  S.data.sections.splice(si, 1);
  renderSections();
}

function moveRowUp(si, ri) {
  const bars  = S.data.sections[si].bars;
  const pStart = (ri - 1) * 4;
  const tStart = ri * 4;
  const prev   = bars.slice(pStart, tStart);
  const curr   = bars.slice(tStart, tStart + 4);
  S.data.sections[si].bars = [
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
  const bars = S.data.sections[si].bars;
  S.data.sections[si].bars = [
    ...bars.slice(0, ri * 4),
    ...bars.slice(ri * 4 + 4),
  ];
  renderSections();
}

function deleteBar(si, bi) {
  S.data.sections[si].bars.splice(bi, 1);
  renderSections();
}

function addRow(si) {
  const empty = Array.from({ length: 4 }, () => ({ bar: 0, beats: {} }));
  S.data.sections[si].bars.push(...empty);
  renderSections();
  setDirty();
}

function addBar(si) {
  S.data.sections[si].bars.push({ bar: 0, beats: {} });
  renderSections();
  setDirty();
}

function addSection() {
  const name = prompt('New section name:', 'C');
  if (name === null) return;
  collectFromDOM();
  S.data.sections.push({
    name:  name.trim() || 'C',
    bars:  Array.from({ length: 4 }, () => ({ bar: 0, beats: {} })),
  });
  renderSections();
  setDirty();
  // Scroll to new section
  setTimeout(() => {
    const cards = qsa('.sec-card');
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
    toast('Saved ✓', 'success');
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
    if (t) t.verified = true;
    renderSidebar();
    updateVerifyButtons();
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
    renderSidebar();
    updateVerifyButtons();
    toast('Verification removed', 'info');
  } catch (err) {
    toast(`Failed: ${err.message}`, 'error');
  }
}

// ─── Verify buttons state ─────────────────────────────────────────────────────
function updateVerifyButtons() {
  const verified = S.tunes.find(t => t.id === S.currentId)?.verified ?? false;
  qs('#btn-verify').classList.toggle('hidden', verified);
  qs('#btn-unverify').classList.toggle('hidden', !verified);
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
    S.data      = { ...raw, sections: sectionsToArray(raw.sections) };
    S.dirty     = false;

    // Sync verified state from server response
    const t = S.tunes.find(t => t.id === id);
    if (t) t.verified = result.verified;

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
  updateVerifyButtons();
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

  // ── Save / Verify buttons
  qs('#btn-save').addEventListener('click',     () => doSave());
  qs('#btn-verify').addEventListener('click',   () => doVerify());
  qs('#btn-unverify').addEventListener('click', () => doUnverify());
  qs('#btn-add-section').addEventListener('click', () => addSection());

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

  // ── Sections: dirty on any input; re-flag duplicates when a name changes
  qs('#sections-container').addEventListener('input', e => {
    setDirty();
    if (e.target.classList.contains('sec-name')) refreshDuplicateFlags();
  });

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
    renderSidebar();

    const startId = result.last_opened || result.tunes[0]?.id;
    if (startId) await openTune(startId);
    else qs('#no-tune').textContent = 'No tune files found in tunes/.';
  } catch (err) {
    console.error('Init failed:', err);
    toast('Failed to load tune list', 'error');
  }
}

init();
