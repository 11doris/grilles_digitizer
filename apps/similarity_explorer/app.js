/* Similarity explorer (tune_similarity_spec §7) — debug/quality tool.
 *
 * Data: window.EXPLORER_DATA from data/explorer_data.js (built by
 * build_data.py). Ratings live in localStorage and export as the §5.2 JSON
 * consumed by `evaluate.py --ingest-ratings`; confirmation mode iterates the
 * ground truth's candidate entries — confirmations are just ratings on
 * candidate pairs.
 */
"use strict";

const DATA = window.EXPLORER_DATA;
const TUNES = DATA.tunes;
const STORE_KEY = "similarity_ratings_v1";

const state = {
  currentId: null,
  view: "tunes",            // tunes | sections | confirm
  selected: null,           // selected match object
  minScore: 0,
  sameFamily: false,
  crossTune: true,
  confirmQueue: [],         // [{family, level, a, b}]
  confirmPos: 0,
  ratings: JSON.parse(localStorage.getItem(STORE_KEY) || "{}"),
};

const $ = (sel) => document.querySelector(sel);

/* -------------------------------------------------------------- ratings -- */

function memberKey(level, m) {
  return level === "tune" ? m : `${m.tune}::${m.section}`;
}

function ratingKey(level, q, c) {
  const [a, b] = [memberKey(level, q), memberKey(level, c)].sort();
  return `${level}|${a}|${b}`;
}

function getRating(level, q, c) {
  const r = state.ratings[ratingKey(level, q, c)];
  return r ? r.rating : null;
}

function setRating(level, q, c, rating) {
  const key = ratingKey(level, q, c);
  if (rating === null) delete state.ratings[key];
  else state.ratings[key] = { level, query: q, candidate: c, rating };
  localStorage.setItem(STORE_KEY, JSON.stringify(state.ratings));
  $("#rating-count").textContent = Object.keys(state.ratings).length;
}

function exportRatings() {
  const payload = {
    exported: new Date().toISOString(),
    ratings: Object.values(state.ratings),
  };
  const blob = new Blob([JSON.stringify(payload, null, 1)],
                        { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ratings_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ----------------------------------------------------------------- list -- */

function renderTuneList() {
  const term = $("#search").value.trim().toUpperCase();
  const ul = $("#tune-list");
  ul.innerHTML = "";
  for (const [id, t] of Object.entries(TUNES)) {
    if (term && !t.title.toUpperCase().includes(term)) continue;
    const li = document.createElement("li");
    li.dataset.id = id;
    li.className = id === state.currentId ? "current" : "";
    li.innerHTML = `<span class="li-title"></span><span class="li-fam"></span>`;
    li.querySelector(".li-title").textContent = t.title;
    li.querySelector(".li-fam").textContent = t.family || "";
    li.addEventListener("click", () => selectTune(id));
    ul.appendChild(li);
  }
}

function selectTune(id) {
  state.currentId = id;
  state.selected = null;
  renderTuneList();
  renderHeader();
  renderMatches();
  renderCompare();
}

function renderHeader() {
  const t = TUNES[state.currentId];
  if (!t) return;
  $("#tune-title").textContent = t.title;
  const key = `${t.key.tonic} ${t.key.mode}`;
  $("#tune-meta").textContent =
    ` ${key} · ${t.form || "?"} · ${t.family || "no family"}`;
}

/* -------------------------------------------------------------- matches -- */

function fmtLocal(lk) {
  return lk ? `locally in ${lk.tonic} ${lk.mode}` : "";
}

function visibleMatches() {
  const t = TUNES[state.currentId];
  if (!t) return [];
  if (state.view === "tunes") {
    return t.similar.filter((s) =>
      s.score >= state.minScore &&
      (!state.sameFamily || s.family === t.family));
  }
  return t.section_matches.filter((m) =>
    m.score >= state.minScore &&
    (!state.crossTune || m.other !== state.currentId) &&
    (!state.sameFamily || (TUNES[m.other] || {}).family === t.family));
}

function renderMatches() {
  const ul = $("#match-list");
  ul.innerHTML = "";
  if (state.view === "confirm") { renderConfirmList(); return; }
  const matches = visibleMatches();
  for (const m of matches) {
    const li = document.createElement("li");
    const isTune = state.view === "tunes";
    const other = TUNES[m.other || m.id] || {};
    const level = isTune ? "tune" : "section";
    const q = isTune ? state.currentId
                     : { tune: state.currentId, section: m.section };
    const c = isTune ? m.id : { tune: m.other, section: m.other_section };
    const comp = m.components;
    li.className = state.selected === m ? "selected" : "";
    li.innerHTML = `
      <span class="score" title="cosine ${comp.cosine} · alignment ${comp.alignment}
meter ×${comp.meter_penalty} · mode ×${comp.mode_penalty}">${m.score.toFixed(2)}</span>
      <span class="m-main"></span>
      <span class="m-fam"></span>
      <span class="rate">
        <button class="r-good" title="good match">👍</button>
        <button class="r-bad" title="bad match">👎</button>
      </span>`;
    const main = li.querySelector(".m-main");
    if (isTune) {
      main.textContent = m.title;
    } else {
      main.textContent =
        `${m.section} ≈ ${m.other_section} of ${m.other_title}`;
      const badges = [fmtLocal(m.local_key), fmtLocal(m.other_local_key)]
        .filter(Boolean);
      if (badges.length) {
        const b = document.createElement("span");
        b.className = "badge local";
        b.textContent = badges.join(" / ");
        main.appendChild(b);
      }
    }
    li.querySelector(".m-fam").textContent = isTune ? (m.family || "") : "";
    const paint = () => {
      const r = getRating(level, q, c);
      li.querySelector(".r-good").classList.toggle("on", r === "good");
      li.querySelector(".r-bad").classList.toggle("on", r === "bad");
    };
    li.querySelector(".r-good").addEventListener("click", (e) => {
      e.stopPropagation();
      setRating(level, q, c, getRating(level, q, c) === "good" ? null : "good");
      paint();
    });
    li.querySelector(".r-bad").addEventListener("click", (e) => {
      e.stopPropagation();
      setRating(level, q, c, getRating(level, q, c) === "bad" ? null : "bad");
      paint();
    });
    paint();
    li.addEventListener("click", () => {
      state.selected = m;
      renderMatches();
      renderCompare();
    });
    ul.appendChild(li);
  }
  if (!matches.length) ul.innerHTML = '<li class="muted">no matches above filters</li>';
}

/* ---------------------------------------------------------------- grids -- */

function gridHTML(id, opts) {
  // opts: {sections: [names] | null, mapped: Map(bar -> [counterpart bars]),
  //        side, localRoman: bool}
  const t = TUNES[id];
  const holder = document.createElement("div");
  const title = document.createElement("div");
  title.className = "g-title";
  title.textContent = `${t.title} — ${t.key.tonic} ${t.key.mode}`;
  holder.appendChild(title);
  for (const [name, sec] of Object.entries(t.grid)) {
    if (opts.sections && !opts.sections.includes(name)) continue;
    const wrap = document.createElement("div");
    wrap.className = "g-section";
    const head = document.createElement("div");
    head.className = "g-sec-head";
    head.textContent = name + (sec.local_key ? "" : "");
    if (sec.local_key) {
      const b = document.createElement("span");
      b.className = "badge local";
      b.textContent = fmtLocal(sec.local_key);
      head.appendChild(b);
    }
    wrap.appendChild(head);
    const caption = (t.fingerprint_sections || {})[name];
    const grid = document.createElement("div");
    grid.className = "g-bars";
    for (const bar of sec.bars) {
      // bar number within the comparison space: whole-tune view uses the
      // flattened bar number, section view the in-section bar number
      const barNo = opts.sections ? bar.bar : sec.start_bar + bar.bar;
      const div = document.createElement("div");
      div.className = "g-bar";
      div.dataset.side = opts.side;
      div.dataset.bar = barNo;
      if (opts.mapped && opts.mapped.has(barNo)) {
        div.classList.add("hl");
        div.dataset.counterparts = opts.mapped.get(barNo).join(",");
      }
      const roman = (c) =>
        (opts.localRoman && c.local_roman) ? c.local_roman : (c.roman || "");
      const cells = bar.slots.map((c) =>
        `<span class="cell" title="${c.sym}">${roman(c)}</span>`);
      div.innerHTML = (bar.slots.length === 2 &&
                       cells[0] === cells[1]) ? cells[0] : cells.join("");
      grid.appendChild(div);
    }
    wrap.appendChild(grid);
    if (caption) {
      const cap = document.createElement("div");
      cap.className = "g-caption";
      cap.textContent = caption;
      wrap.appendChild(cap);
    }
    holder.appendChild(wrap);
  }
  return holder;
}

function barMap(bars, from, to) {
  const map = new Map();
  for (const [q, c] of bars) {
    const a = from === 0 ? q : c, b = from === 0 ? c : q;
    if (!map.has(a)) map.set(a, []);
    map.get(a).push(b);
  }
  return map;
}

function renderCompare() {
  const q = $("#side-q"), c = $("#side-c");
  q.innerHTML = ""; c.innerHTML = "";
  const m = state.selected;
  $("#compare-empty").hidden = !!m;
  if (!m) return;
  if (state.view === "tunes" || m._confirmLevel === "tune") {
    const otherId = m.id || m._b;
    const qId = m._a || state.currentId;
    q.appendChild(gridHTML(qId, {
      sections: null, mapped: m.bars && barMap(m.bars, 0), side: "q" }));
    c.appendChild(gridHTML(otherId, {
      sections: null, mapped: m.bars && barMap(m.bars, 1), side: "c" }));
  } else {
    const qId = m._a ? m._a.tune : state.currentId;
    const qSec = m._a ? m._a.section : m.section;
    const cId = m._b ? m._b.tune : m.other;
    const cSec = m._b ? m._b.section : m.other_section;
    q.appendChild(gridHTML(qId, {
      sections: [qSec], mapped: m.bars && barMap(m.bars, 0),
      side: "q", localRoman: true }));
    c.appendChild(gridHTML(cId, {
      sections: [cSec], mapped: m.bars && barMap(m.bars, 1),
      side: "c", localRoman: true }));
  }
  attachHover();
}

function attachHover() {
  for (const bar of document.querySelectorAll(".g-bar.hl")) {
    bar.addEventListener("mouseenter", () => {
      const others = (bar.dataset.counterparts || "").split(",");
      const otherSide = bar.dataset.side === "q" ? "c" : "q";
      for (const el of document.querySelectorAll(
          `.g-bar[data-side="${otherSide}"]`)) {
        el.classList.toggle("hl-active", others.includes(el.dataset.bar));
      }
      bar.classList.add("hl-active");
    });
    bar.addEventListener("mouseleave", () => {
      for (const el of document.querySelectorAll(".hl-active"))
        el.classList.remove("hl-active");
    });
  }
}

/* --------------------------------------------------------- confirm mode -- */

function buildConfirmQueue() {
  const queue = [];
  for (const fam of (DATA.groundtruth.families || [])) {
    if (fam.status !== "candidate") continue;
    const members = fam.members;
    for (let i = 0; i < members.length; i++) {
      for (let j = i + 1; j < members.length; j++) {
        const a = members[i], b = members[j];
        if (fam.level === "tune" && (!TUNES[a] || !TUNES[b])) continue;
        if (fam.level === "section" && (!TUNES[a.tune] || !TUNES[b.tune])) continue;
        if (getRating(fam.level, a, b)) continue;  // already judged
        queue.push({ family: fam.name, level: fam.level, a, b });
      }
    }
  }
  return queue;
}

function renderConfirmList() {
  const ul = $("#match-list");
  ul.innerHTML = "";
  state.confirmQueue = buildConfirmQueue();
  state.confirmPos = Math.min(state.confirmPos, Math.max(0, state.confirmQueue.length - 1));
  state.confirmQueue.forEach((p, i) => {
    const li = document.createElement("li");
    li.className = i === state.confirmPos ? "selected" : "";
    const fmt = (m) => p.level === "tune"
      ? (TUNES[m] || {}).title || m : `${m.section} of ${(TUNES[m.tune] || {}).title}`;
    li.innerHTML = `<span class="m-main"></span><span class="m-fam"></span>`;
    li.querySelector(".m-main").textContent = `${fmt(p.a)} ↔ ${fmt(p.b)}`;
    li.querySelector(".m-fam").textContent = p.family;
    li.addEventListener("click", () => { state.confirmPos = i; renderConfirm(); });
    ul.appendChild(li);
  });
  if (!state.confirmQueue.length)
    ul.innerHTML = '<li class="muted">no unjudged candidate pairs 🎉</li>';
  renderConfirm();
}

function renderConfirm() {
  const pair = state.confirmQueue[state.confirmPos];
  $("#confirm-bar").hidden = state.view !== "confirm" || !pair;
  if (!pair) { state.selected = null; renderCompare(); return; }
  $("#confirm-pos").textContent =
    `${state.confirmPos + 1} / ${state.confirmQueue.length}`;
  $("#confirm-family").textContent = pair.family;
  state.selected = {
    _confirmLevel: pair.level, _a: pair.a, _b: pair.b, bars: null,
  };
  renderCompare();
  // repaint list selection
  const items = $("#match-list").children;
  for (let i = 0; i < items.length; i++)
    items[i].classList.toggle("selected", i === state.confirmPos);
}

function judgeConfirm(rating) {
  const pair = state.confirmQueue[state.confirmPos];
  if (!pair) return;
  setRating(pair.level, pair.a, pair.b, rating);
  renderConfirmList();  // rebuilds the queue without the judged pair
}

/* ----------------------------------------------------------------- init -- */

function setView(v) {
  state.view = v;
  state.selected = null;
  for (const b of $("#view-tabs").querySelectorAll("button"))
    b.classList.toggle("active", b.dataset.view === v);
  $("#confirm-bar").hidden = v !== "confirm";
  renderMatches();
  renderCompare();
}

for (const b of $("#view-tabs").querySelectorAll("button"))
  b.addEventListener("click", () => setView(b.dataset.view));

$("#search").addEventListener("input", renderTuneList);
$("#btn-export").addEventListener("click", exportRatings);
$("#f-minscore").addEventListener("input", () => {
  state.minScore = parseFloat($("#f-minscore").value);
  $("#f-minscore-val").textContent = state.minScore.toFixed(2);
  renderMatches();
});
$("#f-samefamily").addEventListener("change", (e) => {
  state.sameFamily = e.target.checked; renderMatches();
});
$("#f-crosstune").addEventListener("change", (e) => {
  state.crossTune = e.target.checked; renderMatches();
});
$("#btn-good").addEventListener("click", () => judgeConfirm("good"));
$("#btn-bad").addEventListener("click", () => judgeConfirm("bad"));
$("#btn-skip").addEventListener("click", () => {
  state.confirmPos = Math.min(state.confirmPos + 1,
                              state.confirmQueue.length - 1);
  renderConfirm();
});

document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (state.view !== "confirm") return;
  if (e.key === "g" || e.key === "G") judgeConfirm("good");
  else if (e.key === "b" || e.key === "B") judgeConfirm("bad");
  else if (e.key === "ArrowRight") {
    state.confirmPos = Math.min(state.confirmPos + 1, state.confirmQueue.length - 1);
    renderConfirm();
  } else if (e.key === "ArrowLeft") {
    state.confirmPos = Math.max(state.confirmPos - 1, 0);
    renderConfirm();
  }
});

$("#engine-info").textContent = DATA.engine
  ? `engine ${DATA.engine.engine_version} · ${DATA.engine.corpus} tunes · built ${DATA.built}`
  : "";
$("#rating-count").textContent = Object.keys(state.ratings).length;
renderTuneList();
selectTune(Object.keys(TUNES)[0]);
