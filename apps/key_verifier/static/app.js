/* Key verifier UI (tune_similarity_spec §3.6).
 *
 * State model: the server's annotated document is the source of truth; the
 * panel holds a working copy of {tonic, mode, section_keys, fingerprint}.
 * "Verify" posts the working copy; the server routes it through the shared
 * update routine (opening/section-key recompute) and returns the saved doc.
 */
"use strict";

const TONICS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

// Prefer flat spelling for the enharmonic tritone tonic (project convention).
function normTonic(t) { return t === "F#" ? "Gb" : t; }

const state = {
  tunes: [],          // /api/tunes entries
  filter: "needs_review",
  currentId: null,
  doc: null,          // full annotated document of the current tune
  edit: null,         // working copy: {tonic, mode, sectionKeys, fingerprint}
};

const $ = (sel) => document.querySelector(sel);

/* ---------------------------------------------------------------- list -- */

function statusRank(s) { return { needs_review: 0, agreed: 1, verified: 2 }[s] ?? 0; }

function visibleTunes() {
  const f = state.filter;
  let out = state.tunes;
  if (f === "needs_review") out = out.filter((t) => t.status === "needs_review");
  else if (f === "unverified") out = out.filter((t) => t.status !== "verified");
  // default order: needs_review first, then unverified agreed (spec §3.6)
  return [...out].sort((a, b) =>
    statusRank(a.status) - statusRank(b.status) || a.id.localeCompare(b.id));
}

function setFilter(f) {
  state.filter = f;
  for (const b of $("#filter-tabs").querySelectorAll("button"))
    b.classList.toggle("active", b.dataset.filter === f);
}

async function loadList(keepCurrent = true) {
  const data = await (await fetch("/api/tunes")).json();
  state.tunes = data.tunes;
  const c = data.counts;
  const remaining = data.total - c.verified;
  $("#progress").textContent =
    `${c.verified} verified / ${c.needs_review} needs review / ${remaining} remaining`;
  // on first load, fall through to the widest non-empty tab
  if (!keepCurrent && !visibleTunes().length) {
    setFilter(state.filter === "needs_review" && c.verified < data.total
              ? "unverified" : "all");
  }
  renderList();
  const vis = visibleTunes();
  if (!keepCurrent || !state.currentId ||
      !vis.some((t) => t.id === state.currentId)) {
    if (vis.length) selectTune(vis[0].id);
  }
}

function renderList() {
  const ul = $("#tune-list");
  ul.innerHTML = "";
  for (const t of visibleTunes()) {
    const li = document.createElement("li");
    li.dataset.id = t.id;
    li.className = `status-${t.status}` + (t.id === state.currentId ? " current" : "");
    const key = t.key ? `${normTonic(t.key.tonic)}${t.key.mode === "minor" ? "m" : ""}` : "?";
    li.innerHTML = `<span class="dot"></span><span class="li-title"></span>
                    <span class="li-key">${key}</span>`;
    li.querySelector(".li-title").textContent = t.title;
    li.addEventListener("click", () => selectTune(t.id));
    ul.appendChild(li);
  }
}

/* --------------------------------------------------------------- panel -- */

async function selectTune(id) {
  const data = await (await fetch(`/api/tunes/${id}`)).json();
  state.currentId = id;
  state.doc = data.data;
  const fp = state.doc.harmonic_fingerprint || {};
  // §3.5 re-detected local keys: proposals from the last key correction,
  // shown for accept/dismiss; any Verify save clears them server-side.
  state.proposals = { ...((state.doc.key_annotation || {}).section_key_proposals || {}) };
  state.edit = {
    tonic: normTonic(state.doc.key.tonic),
    mode: state.doc.key.mode,
    sectionKeys: Object.fromEntries(
      Object.entries(state.doc.section_keys || {}).map(
        ([n, k]) => [n, { ...k, tonic: normTonic(k.tonic) }])),
    fingerprint: {
      family: fp.family || "",
      tags: [...(fp.tags || [])],
      sections: { ...(fp.sections || {}) },
      modulates: !!fp.modulates,
      modulation_note: fp.modulation_note || "",
    },
  };
  renderList();
  renderPanel();
}

function renderPanel() {
  const doc = state.doc, edit = state.edit;
  const ann = doc.key_annotation || {};

  $("#tune-title").textContent = doc.title || state.currentId;
  $("#tune-id").textContent = state.currentId;
  const vis = visibleTunes();
  const pos = vis.findIndex((t) => t.id === state.currentId);
  $("#queue-pos").textContent = pos >= 0 ? `${pos + 1} / ${vis.length} in queue` : "";

  const badge = $("#status-badge");
  badge.textContent = ann.status || "?";
  badge.className = `badge st-${ann.status}`;

  // crop
  const t = state.tunes.find((x) => x.id === state.currentId);
  const img = $("#crop-img");
  if (t && t.has_image) {
    img.src = `/crop/${state.currentId}`;
    img.hidden = false; $("#crop-missing").hidden = true;
  } else {
    img.hidden = true; $("#crop-missing").hidden = false;
  }

  renderKey();
  renderSectionKeys();
  renderVotes();
  renderFingerprint();
}

function renderKey() {
  const { tonic, mode } = state.edit;
  $("#key-tonic").textContent = tonic;
  $("#key-mode").textContent = mode;
  const opening = state.doc.opening;
  $("#opening-badge").textContent = opening ? `starts on ${opening.degree}` : "";

  const picker = $("#tonic-picker");
  picker.innerHTML = "";
  for (const t of TONICS) {
    const b = document.createElement("button");
    b.textContent = t;
    b.className = t === tonic ? "active" : "";
    b.addEventListener("click", () => { state.edit.tonic = t; renderKey(); });
    picker.appendChild(b);
  }
  for (const b of $("#mode-toggle").querySelectorAll("button")) {
    b.classList.toggle("active", b.dataset.mode === mode);
    b.onclick = () => { state.edit.mode = b.dataset.mode; renderKey(); };
  }
}

function renderSectionKeys() {
  const holder = $("#section-keys");
  holder.innerHTML = "";
  const sections = Object.keys(state.doc.sections || {});
  for (const name of sections) {
    const local = state.edit.sectionKeys[name];
    const row = document.createElement("div");
    row.className = "sk-row";
    const tonicOpts = TONICS.map((t) =>
      `<option value="${t}" ${local && local.tonic === t ? "selected" : ""}>${t}</option>`).join("");
    row.innerHTML = `
      <span class="sk-name"></span>
      <label class="row"><input type="checkbox" class="sk-on" ${local ? "checked" : ""}> local key</label>
      <select class="sk-tonic" ${local ? "" : "disabled"}>${tonicOpts}</select>
      <select class="sk-mode" ${local ? "" : "disabled"}>
        <option value="major" ${!local || local.mode === "major" ? "selected" : ""}>major</option>
        <option value="minor" ${local && local.mode === "minor" ? "selected" : ""}>minor</option>
      </select>`;
    row.querySelector(".sk-name").textContent = name;
    const sync = () => {
      const on = row.querySelector(".sk-on").checked;
      row.querySelector(".sk-tonic").disabled = !on;
      row.querySelector(".sk-mode").disabled = !on;
      if (on) {
        state.edit.sectionKeys[name] = {
          tonic: row.querySelector(".sk-tonic").value,
          mode: row.querySelector(".sk-mode").value,
        };
      } else {
        delete state.edit.sectionKeys[name];
      }
    };
    for (const el of row.querySelectorAll("input,select")) el.addEventListener("change", sync);
    holder.appendChild(row);
  }
  if (!sections.length) holder.innerHTML = '<span class="muted">no sections</span>';
  renderProposals(holder);
}

function renderProposals(holder) {
  // Local keys re-detected by the scorer after a key correction (spec §3.5):
  // never auto-applied — the human accepts (into section_keys) or dismisses.
  const pending = Object.entries(state.proposals || {})
    .filter(([name]) => !state.edit.sectionKeys[name]);
  if (!pending.length) return;
  const box = document.createElement("div");
  box.id = "sk-proposals";
  box.innerHTML = '<div class="prop-title">re-detected under the corrected key — accept?</div>';
  for (const [name, k] of pending) {
    const row = document.createElement("div");
    row.className = "prop-row";
    row.innerHTML = `<span class="prop-text"></span>
      <button class="prop-accept">accept</button>
      <button class="prop-dismiss">dismiss</button>`;
    row.querySelector(".prop-text").textContent =
      `${name}: ${normTonic(k.tonic)} ${k.mode}` +
      (k.margin != null ? ` (margin ${Number(k.margin).toFixed(2)})` : "");
    row.querySelector(".prop-accept").addEventListener("click", () => {
      state.edit.sectionKeys[name] = { tonic: normTonic(k.tonic), mode: k.mode };
      delete state.proposals[name];
      renderSectionKeys();
    });
    row.querySelector(".prop-dismiss").addEventListener("click", () => {
      delete state.proposals[name];
      renderSectionKeys();
    });
    box.appendChild(row);
  }
  holder.appendChild(box);
}

function fmtKey(k) { return k ? `${normTonic(k.tonic)} ${k.mode}` : "—"; }

function fmtSectionKeys(sk) {
  const entries = Object.entries(sk || {});
  if (!entries.length) return "";
  return " · sections: " + entries.map(([n, k]) => `${n}=${fmtKey(k)}`).join(", ");
}

function renderVotes() {
  const ann = state.doc.key_annotation || {};
  const holder = $("#votes");
  const scorer = ann.scorer, llm = ann.llm;
  const disagree = scorer && llm && !llm.error &&
    (fmtKey(scorer) !== fmtKey(llm));
  const rows = [];
  if (scorer) {
    rows.push(`<div class="vote ${disagree ? "disagree" : ""}">
      <span class="vote-name">scorer</span> ${fmtKey(scorer)}
      <span class="muted">margin ${scorer.margin}</span>${fmtSectionKeys(scorer.section_keys)}</div>`);
  }
  if (llm) {
    rows.push(llm.error
      ? `<div class="vote disagree"><span class="vote-name">llm</span> failed: ${llm.error}</div>`
      : `<div class="vote ${disagree ? "disagree" : ""}">
          <span class="vote-name">llm</span> ${fmtKey(llm)}
          <span class="muted">${llm.confidence} confidence</span>
          ${llm.modulation_note ? `<span class="muted">· ${llm.modulation_note}</span>` : ""}
          ${fmtSectionKeys(llm.section_keys)}</div>`);
  }
  holder.innerHTML = rows.join("");
  const ul = $("#review-reasons");
  ul.innerHTML = (ann.review_reasons || []).map(() => "<li></li>").join("");
  (ann.review_reasons || []).forEach((r, i) => { ul.children[i].textContent = r; });
}

function renderFingerprint() {
  const fp = state.edit.fingerprint;
  $("#fp-stale").hidden = !(state.doc.harmonic_fingerprint || {}).stale;
  $("#fp-family").value = fp.family;
  $("#fp-tags").value = fp.tags.join(", ");
  $("#fp-modulates").checked = fp.modulates;
  $("#fp-modnote").value = fp.modulation_note;
  const holder = $("#fp-sections");
  holder.innerHTML = "";
  for (const name of Object.keys(state.doc.sections || {})) {
    const label = document.createElement("label");
    label.append(`section ${name}`);
    const ta = document.createElement("textarea");
    ta.rows = 2;
    ta.value = fp.sections[name] || "";
    ta.addEventListener("input", () => { fp.sections[name] = ta.value; });
    label.appendChild(ta);
    holder.appendChild(label);
  }
}

/* -------------------------------------------------------------- verify -- */

async function verifyCurrent() {
  if (!state.currentId) return;
  const fp = state.edit.fingerprint;
  const body = {
    tonic: state.edit.tonic,
    mode: state.edit.mode,
    section_keys: state.edit.sectionKeys,
    fingerprint: {
      family: $("#fp-family").value.trim(),
      tags: $("#fp-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
      sections: fp.sections,
      modulates: $("#fp-modulates").checked,
      modulation_note: $("#fp-modnote").value.trim(),
    },
  };
  const resp = await fetch(`/api/tunes/${state.currentId}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    alert(`save failed: ${(await resp.json()).error || resp.status}`);
    return;
  }
  const verifiedId = state.currentId;
  const vis = visibleTunes();
  const pos = vis.findIndex((t) => t.id === verifiedId);
  await loadList();
  // move on to the next tune in the (possibly shrunken) queue
  const after = visibleTunes();
  if (after.length) {
    const next = after.find((t, i) => i >= pos && t.id !== verifiedId) || after[0];
    if (next.id !== state.currentId) selectTune(next.id);
    else renderPanel();
  } else {
    selectTune(verifiedId);  // queue empty: show what we just saved
  }
}

function step(delta) {
  const vis = visibleTunes();
  if (!vis.length) return;
  const pos = vis.findIndex((t) => t.id === state.currentId);
  const next = vis[(pos + delta + vis.length) % vis.length];
  selectTune(next.id);
}

/* ---------------------------------------------------------------- init -- */

for (const btn of $("#filter-tabs").querySelectorAll("button")) {
  btn.addEventListener("click", () => {
    setFilter(btn.dataset.filter);
    renderList();
    const vis = visibleTunes();
    if (vis.length && !vis.some((t) => t.id === state.currentId)) selectTune(vis[0].id);
  });
}
$("#btn-verify").addEventListener("click", verifyCurrent);
$("#btn-prev").addEventListener("click", () => step(-1));
$("#btn-next").addEventListener("click", () => step(1));

document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === "v" || e.key === "V") { e.preventDefault(); verifyCurrent(); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
});

loadList(false);
