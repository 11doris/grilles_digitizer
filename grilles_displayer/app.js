/* Grilles Displayer — search, navigation and grid rendering (spec §5, §6, §8, §9). */
"use strict";

(function () {
  const { renderChordHTML, escapeHtml } = window.GrillesChords;
  const TUNES = window.TUNES || [];

  const searchEl = document.getElementById("search");
  const listEl = document.getElementById("tuneList");
  const viewEl = document.getElementById("tuneView");
  const themeBtn = document.getElementById("themeToggle");

  const state = {
    filtered: TUNES,
    activeIndex: -1, // keyboard highlight within filtered list
    currentId: null, // tune displayed in the main panel
  };

  /* ---------------------------------------------------------------- theme */

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    themeBtn.textContent = theme === "dark" ? "☀" : "☾";
  }

  function initTheme() {
    let theme = null;
    try {
      theme = localStorage.getItem("grilles.theme");
    } catch (e) { /* storage unavailable (e.g. some file:// contexts) */ }
    if (theme !== "dark" && theme !== "light") {
      theme = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }
    applyTheme(theme);
  }

  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try {
      localStorage.setItem("grilles.theme", next);
    } catch (e) { /* ignore */ }
  });

  /* ---------------------------------------------------------------- search */

  function normalize(s) {
    return String(s)
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase();
  }

  function filterTunes(query) {
    const terms = normalize(query).split(/\s+/).filter(Boolean);
    if (!terms.length) return TUNES;
    return TUNES.filter((t) => {
      const hay = normalize((t.title || "") + " " + (t.composer || ""));
      return terms.every((term) => hay.includes(term));
    });
  }

  searchEl.addEventListener("input", () => {
    state.filtered = filterTunes(searchEl.value);
    state.activeIndex = state.filtered.length ? 0 : -1;
    renderList();
  });

  /* ------------------------------------------------------------ tune list */

  function renderList() {
    listEl.innerHTML = "";
    if (!state.filtered.length) {
      const empty = document.createElement("div");
      empty.className = "list-empty";
      empty.textContent = "No tunes found";
      listEl.appendChild(empty);
      return;
    }
    state.filtered.forEach((tune, i) => {
      const item = document.createElement("div");
      item.className = "tune-item";
      if (tune.id === state.currentId) item.classList.add("current");
      if (i === state.activeIndex) item.classList.add("active");
      item.innerHTML =
        `<div class="t">${escapeHtml(tune.title || tune.id)}</div>` +
        (tune.composer ? `<div class="c">${escapeHtml(tune.composer)}</div>` : "");
      item.addEventListener("click", () => openTune(tune.id));
      listEl.appendChild(item);
    });
  }

  function moveActive(delta) {
    if (!state.filtered.length) return;
    const n = state.filtered.length;
    state.activeIndex = ((state.activeIndex + delta) % n + n) % n;
    renderList();
    const el = listEl.querySelector(".tune-item.active");
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveActive(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveActive(-1);
    } else if (e.key === "Enter") {
      const tune = state.filtered[state.activeIndex];
      if (tune) openTune(tune.id);
    } else if (e.key === "Escape") {
      if (searchEl.value) {
        searchEl.value = "";
        searchEl.dispatchEvent(new Event("input"));
      }
      searchEl.focus();
    }
  });

  /* ------------------------------------------------------------ navigation */

  function tuneById(id) {
    return TUNES.find((t) => t.id === id) || null;
  }

  function openTune(id) {
    if (location.hash.slice(1) !== id) {
      location.hash = id; // triggers hashchange → renderTune
    } else {
      renderTune(id);
    }
  }

  window.addEventListener("hashchange", () => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (tuneById(id)) renderTune(id);
  });

  /* -------------------------------------------------------- grid rendering */

  function el(tag, cls) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    return node;
  }

  function beatsPerBar(tune) {
    const num = parseInt(String(tune.time_signature || "4/4").split("/")[0], 10);
    return Number.isFinite(num) && num > 0 ? num : 4;
  }

  /* Spec §6.2: "A1" → "A"; "verse_A" → "Verse A". */
  function displaySectionName(name) {
    const m = /^([A-Z])\d*$/.exec(name);
    if (m) return m[1];
    const s = name.replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function fillBar(cell, barObj, beats) {
    cell.style.gridTemplateColumns = `repeat(${beats}, 1fr)`;
    const entries = Object.entries(barObj.beats || {})
      .map(([k, v]) => [parseInt(k, 10), v])
      .filter(([k]) => Number.isFinite(k) && k >= 1 && k <= beats)
      .sort((a, b) => a[0] - b[0]);
    entries.forEach(([beat, chord], idx) => {
      const next = idx + 1 < entries.length ? entries[idx + 1][0] : beats + 1;
      const slot = el("div", "slot");
      slot.style.gridColumn = `${beat} / ${next}`;
      slot.innerHTML = renderChordHTML(chord);
      cell.appendChild(slot);
    });
  }

  function timesigEl(ts) {
    const [num, den] = String(ts || "4/4").split("/");
    const box = el("span", "timesig");
    box.innerHTML = `<span>${escapeHtml(num)}</span><span>${escapeHtml(den || "")}</span>`;
    return box;
  }

  /*
   * Rows of exactly 4 bar slots (spec §6.1). Trailing slots of an incomplete
   * last row are empty: no chords, no barlines.
   * opts: { double: bool, timesig: string|null }
   */
  function renderGrid(bars, beats, opts) {
    const double = opts && opts.double !== undefined ? opts.double : true;
    const frag = document.createDocumentFragment();
    for (let start = 0; start < bars.length; start += 4) {
      const row = el("div", "row");
      if (opts && opts.timesig && start === 0) row.appendChild(timesigEl(opts.timesig));
      for (let i = 0; i < 4; i++) {
        const idx = start + i;
        const bar = bars[idx];
        const cell = el("div", "bar");
        if (bar) {
          if (double && idx === 0) cell.classList.add("sec-start");
          if (double && idx === bars.length - 1) cell.classList.add("sec-end");
          if (i === 3 || idx === bars.length - 1) cell.classList.add("rowlast");
          fillBar(cell, bar, beats);
        } else {
          cell.classList.add("empty");
        }
        row.appendChild(cell);
      }
      frag.appendChild(row);
    }
    return frag;
  }

  function renderSection(name, bars, beats, isFirst, ts) {
    const sec = el("div", "section");
    const badge = el("div", "sec-label");
    badge.textContent = displaySectionName(name);
    sec.appendChild(badge);
    sec.appendChild(renderGrid(bars, beats, { double: true, timesig: isFirst ? ts : null }));
    return sec;
  }

  /* ------------------------------------------------------------ tune head */

  function titleCase(s) {
    return String(s).toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function renderHead(tune) {
    const head = el("div", "tune-head");
    const top = el("div", "head-top");
    const tempo = el("span", "tempo");
    if (tune.tempo) tempo.textContent = `(${titleCase(tune.tempo)})`;
    const title = el("h2", "title");
    title.textContent = tune.title || tune.id;
    const composer = el("span", "composer");
    composer.textContent = tune.composer || "";
    top.append(tempo, title, composer);
    head.appendChild(top);

    const parts = [];
    if (tune.style) parts.push(escapeHtml(tune.style));
    if (tune.year) parts.push(escapeHtml(tune.year));
    if (tune.form) parts.push(escapeHtml(tune.form));
    if (tune.page != null) parts.push(`p. ${escapeHtml(tune.page)}`);
    if (parts.length) {
      const meta = el("div", "meta");
      meta.innerHTML = parts.join(" · ");
      head.appendChild(meta);
    }
    return head;
  }

  /* --------------------------------------------------------------- extras */

  function detailsBlock(summary) {
    const details = el("details");
    const s = el("summary");
    s.textContent = summary;
    details.appendChild(s);
    return details;
  }

  function renderExtras(tune, beats) {
    const extras = el("div", "extras");

    if (Array.isArray(tune.variants) && tune.variants.length) {
      const block = detailsBlock("Variants");
      tune.variants.forEach((variant) => {
        const wrap = el("div", "variant");
        if (variant.applies_to) {
          const cap = el("div", "variant-caption");
          cap.textContent = variant.applies_to;
          wrap.appendChild(cap);
        }
        const mini = el("div", "mini-grid");
        mini.appendChild(renderGrid(variant.bars || [], beats, { double: false }));
        wrap.appendChild(mini);
        block.appendChild(wrap);
      });
      extras.appendChild(block);
    }

    if (Array.isArray(tune.recordings) && tune.recordings.length) {
      const block = detailsBlock("Recordings");
      const ul = el("ul");
      tune.recordings.forEach((r) => {
        const li = el("li");
        li.textContent = r;
        ul.appendChild(li);
      });
      block.appendChild(ul);
      extras.appendChild(block);
    }

    const notes = [];
    if (tune.notation_notes && typeof tune.notation_notes === "object") {
      for (const [key, value] of Object.entries(tune.notation_notes)) {
        notes.push(`${key.replace(/_/g, " ")}: ${value}`);
      }
    }
    if (tune.same_chord_changes) notes.push(`Same changes: ${tune.same_chord_changes}`);
    if (notes.length) {
      const block = detailsBlock("Notes");
      notes.forEach((n) => {
        const p = el("p");
        p.textContent = n;
        block.appendChild(p);
      });
      extras.appendChild(block);
    }

    return extras.childElementCount ? extras : null;
  }

  /* ----------------------------------------------------------- main render */

  function renderTune(id) {
    const tune = tuneById(id);
    if (!tune) return;
    state.currentId = id;
    document.title = `${tune.title || id} — Grilles`;

    const beats = beatsPerBar(tune);
    viewEl.innerHTML = "";
    viewEl.appendChild(renderHead(tune));

    const grid = el("div", "grid");
    const sectionNames = Object.keys(tune.sections || {});
    sectionNames.forEach((name, i) => {
      grid.appendChild(renderSection(name, tune.sections[name], beats, i === 0, tune.time_signature));
    });
    viewEl.appendChild(grid);

    const extras = renderExtras(tune, beats);
    if (extras) viewEl.appendChild(extras);

    viewEl.scrollTop = 0;
    renderList(); // refresh "current" highlight
    requestAnimationFrame(fitChords);
  }

  /* Shrink chords that overflow their beat slots (spec §6.4). */
  function fitChords() {
    viewEl.querySelectorAll(".slot").forEach((slot) => {
      const chord = slot.firstElementChild;
      if (!chord) return;
      chord.style.fontSize = "";
      let size = 1;
      while (size > 0.55 && chord.getBoundingClientRect().width > slot.getBoundingClientRect().width - 2) {
        size -= 0.1;
        chord.style.fontSize = size.toFixed(2) + "em";
      }
    });
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitChords, 150);
  });

  /* ----------------------------------------------------------------- init */

  initTheme();
  renderList();
  searchEl.focus();

  const initialId = decodeURIComponent(location.hash.slice(1));
  if (tuneById(initialId)) {
    renderTune(initialId);
  } else if (TUNES.length) {
    history.replaceState(null, "", "#" + encodeURIComponent(TUNES[0].id));
    renderTune(TUNES[0].id);
  } else {
    viewEl.innerHTML = '<div class="list-empty">No tunes found. Run build_data.py first.</div>';
  }
})();
