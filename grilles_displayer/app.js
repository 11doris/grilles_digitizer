/* Grilles Displayer — search, navigation, panels and grid rendering (spec §5, §6, §8, §9). */
"use strict";

(function () {
  const { renderChordHTML, escapeHtml } = window.GrillesChords;
  const TUNES = window.TUNES || [];

  const searchEl = document.getElementById("search");
  const listEl = document.getElementById("tuneList");
  const viewEl = document.getElementById("tuneView");
  const paneEl = document.getElementById("tunePane");
  const themeBtn = document.getElementById("themeToggle");
  const listToggle = document.getElementById("listToggle");
  const listBackdrop = document.getElementById("listBackdrop");
  const overlayEl = document.getElementById("scanOverlay");
  const overlayImg = document.getElementById("overlayImg");
  const overlayClose = document.getElementById("overlayClose");
  const zoomInBtn = document.getElementById("zoomIn");
  const zoomOutBtn = document.getElementById("zoomOut");

  /* Phones (narrow, or short in landscape): the list is a drawer; from 900px
     wide the visible panels sit side by side. Keep in sync with style.css. */
  const narrowMq = window.matchMedia("(max-width: 700px), (max-height: 500px)");

  const state = {
    filtered: TUNES,
    activeIndex: -1, // keyboard highlight within filtered list
    currentId: null, // tune displayed in the main panel
    showChords: true, // Chords switch (persisted)
    showMelody: false, // Melody switch (persisted)
    chordScan: false, // chord panel showing the original scan instead of the grid
    overlayMag: false, // fullscreen scan magnified (pan by scrolling)
    gridZoom: 1, // user zoom factor applied on top of the fitted grid size
  };

  /* ------------------------------------------------------------- helpers */

  /* Embedded chord JSON (§4.2); empty for non-digitized tunes. */
  function meta(t) {
    return t.tune || {};
  }

  function hasChordAsset(t) {
    return Boolean(t.chord_image || t.has_chord_json);
  }

  function hasMelodyAsset(t) {
    return Boolean(t.melody_image || t.has_melody_abc);
  }

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

  /* ----------------------------------------------------- tune list drawer */

  function setListOpen(open) {
    document.body.classList.toggle("list-open", open);
    listToggle.setAttribute("aria-expanded", String(open));
  }

  listToggle.addEventListener("click", () => {
    setListOpen(!document.body.classList.contains("list-open"));
  });

  listBackdrop.addEventListener("click", () => setListOpen(false));

  /* --------------------------------------------- fullscreen scan overlay */

  function openOverlay(src, alt) {
    state.overlayMag = false;
    overlayImg.src = src;
    overlayImg.alt = alt || "";
    overlayEl.hidden = false;
    overlayEl.classList.remove("magnified");
  }

  /* Returns false when the overlay wasn't open. */
  function closeOverlay() {
    if (overlayEl.hidden) return false;
    overlayEl.hidden = true;
    state.overlayMag = false;
    return true;
  }

  /* Click magnifies the scan (up to natural size, panned by scrolling). */
  overlayImg.addEventListener("click", (e) => {
    e.stopPropagation();
    state.overlayMag = !state.overlayMag;
    overlayEl.classList.toggle("magnified", state.overlayMag);
    if (state.overlayMag) {
      /* Start panning from the middle of the scan. */
      requestAnimationFrame(() => {
        overlayEl.scrollLeft = (overlayEl.scrollWidth - overlayEl.clientWidth) / 2;
        overlayEl.scrollTop = (overlayEl.scrollHeight - overlayEl.clientHeight) / 2;
      });
    }
  });

  overlayEl.addEventListener("click", () => closeOverlay());
  overlayClose.addEventListener("click", (e) => {
    e.stopPropagation();
    closeOverlay();
  });

  /* ---------------------------------------------------------------- search */

  function normalize(s) {
    return String(s)
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase();
  }

  /* Precomputed haystack — filtering ~1,600 rows stays instant (spec §8). */
  TUNES.forEach((t) => {
    t._hay = normalize((t.title || "") + " " + (meta(t).composer || ""));
  });

  function filterTunes(query) {
    const terms = normalize(query).split(/\s+/).filter(Boolean);
    if (!terms.length) return TUNES;
    return TUNES.filter((t) => terms.every((term) => t._hay.includes(term)));
  }

  searchEl.addEventListener("input", () => {
    state.filtered = filterTunes(searchEl.value);
    state.activeIndex = state.filtered.length ? 0 : -1;
    renderList();
    if (narrowMq.matches && searchEl.value) setListOpen(true); // show results on mobile
  });

  /* ------------------------------------------------------------ tune list */

  /* Availability icons (spec §5.2): left slot = chord grille, right slot =
     melody. Green when digitized, gray when only the scan exists, empty
     (reserved) when the tune has no asset of that type. */
  const ICON_GRID =
    '<svg viewBox="0 0 16 16" aria-hidden="true">' +
    '<path d="M1.5 1.5h5.6v5.6H1.5zM8.9 1.5h5.6v5.6H8.9zM1.5 8.9h5.6v5.6H1.5zM8.9 8.9h5.6v5.6H8.9z"/></svg>';
  const ICON_NOTES =
    '<svg viewBox="0 0 16 16" aria-hidden="true">' +
    '<path d="M14.6.7 5.4 2.5v8.2a2.6 2.6 0 0 0-1.2-.3C2.8 10.4 1.6 11.4 1.6 12.6S2.8 14.8 4.2 14.8s2.6-1 2.6-2.2V6.3l6.4-1.25v4.15a2.6 2.6 0 0 0-1.2-.3c-1.4 0-2.6 1-2.6 2.2s1.2 2.2 2.6 2.2 2.6-1 2.6-2.2z"/></svg>';

  function iconCluster(t) {
    const chord = hasChordAsset(t)
      ? `<span class="icon${t.has_chord_json ? " ok" : ""}" title="Chord grid${t.has_chord_json ? " (digitized)" : " (scan)"}">${ICON_GRID}</span>`
      : '<span class="icon none"></span>';
    const melody = hasMelodyAsset(t)
      ? `<span class="icon${t.has_melody_abc ? " ok" : ""}" title="Melody${t.has_melody_abc ? " (digitized)" : " (scan)"}">${ICON_NOTES}</span>`
      : '<span class="icon none"></span>';
    return `<span class="icons">${chord}${melody}</span>`;
  }

  function renderList() {
    if (!state.filtered.length) {
      listEl.innerHTML = '<div class="list-empty">No tunes found</div>';
      return;
    }
    /* One string + one innerHTML: ~1,600 rows render in a few ms. */
    listEl.innerHTML = state.filtered
      .map((t, i) => {
        const composer = meta(t).composer;
        const cls = "tune-item" +
          (t.id === state.currentId ? " current" : "") +
          (i === state.activeIndex ? " active" : "");
        return `<div class="${cls}" data-id="${escapeHtml(t.id)}">${iconCluster(t)}` +
          `<span class="txt"><span class="t">${escapeHtml(t.title || t.id)}</span>` +
          (composer ? `<span class="c">${escapeHtml(composer)}</span>` : "") +
          "</span></div>";
      })
      .join("");
  }

  listEl.addEventListener("click", (e) => {
    const item = e.target.closest(".tune-item");
    if (item) openTune(item.dataset.id);
  });

  /* Move highlight classes without rebuilding 1,600 rows. */
  function updateListHighlight() {
    const items = listEl.children;
    for (let i = 0; i < items.length; i++) {
      if (!items[i].classList) continue;
      items[i].classList.toggle("active", i === state.activeIndex);
      items[i].classList.toggle("current", items[i].dataset.id === state.currentId);
    }
  }

  function moveActive(delta) {
    if (!state.filtered.length) return;
    const n = state.filtered.length;
    state.activeIndex = ((state.activeIndex + delta) % n + n) % n;
    updateListHighlight();
    const el = listEl.querySelector(".tune-item.active");
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("keydown", (e) => {
    /* While a search query is being typed, ↑/↓ keep moving the result
       highlight and ←/→ keep moving the caret; otherwise all arrow and
       page keys flip between tunes. */
    const searching = e.target === searchEl && searchEl.value !== "";
    if (e.key === "ArrowDown" || e.key === "PageDown") {
      e.preventDefault();
      if (searching && e.key === "ArrowDown") moveActive(1);
      else navTune(1);
    } else if (e.key === "ArrowUp" || e.key === "PageUp") {
      e.preventDefault();
      if (searching && e.key === "ArrowUp") moveActive(-1);
      else navTune(-1);
    } else if (e.key === "ArrowRight") {
      if (searching) return;
      e.preventDefault();
      navTune(1);
    } else if (e.key === "ArrowLeft") {
      if (searching) return;
      e.preventDefault();
      navTune(-1);
    } else if (e.key === "Enter") {
      const tune = state.filtered[state.activeIndex];
      if (tune) openTune(tune.id);
    } else if (e.key === "Escape") {
      if (closeOverlay()) return;
      if (document.body.classList.contains("list-open")) {
        setListOpen(false);
        return;
      }
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

  /* Open the tune `delta` places away from the current one, following the
     filtered list order. Stops at either end (no wrap-around). */
  function navTune(delta) {
    const list = state.filtered.length ? state.filtered : TUNES;
    if (!list.length) return;
    const idx = list.findIndex((t) => t.id === state.currentId);
    const next = idx === -1 ? 0 : idx + delta;
    if (next < 0 || next >= list.length) return;
    openTune(list[next].id);
  }

  /* Swipe on the tune view (mobile): left → next tune, right → previous.
     Disabled while the fullscreen scan is open (panned by touch-scrolling). */
  let swipeStart = null;

  viewEl.addEventListener("touchstart", (e) => {
    swipeStart = e.touches.length === 1 && overlayEl.hidden
      ? { x: e.touches[0].clientX, y: e.touches[0].clientY }
      : null;
  }, { passive: true });

  viewEl.addEventListener("touchend", (e) => {
    if (!swipeStart) return;
    const dx = e.changedTouches[0].clientX - swipeStart.x;
    const dy = e.changedTouches[0].clientY - swipeStart.y;
    swipeStart = null;
    /* Require a mostly-horizontal move so vertical scrolling never flips. */
    if (Math.abs(dx) < 60 || Math.abs(dx) < 1.5 * Math.abs(dy)) return;
    navTune(dx < 0 ? 1 : -1);
  }, { passive: true });

  viewEl.addEventListener("touchcancel", () => {
    swipeStart = null;
  }, { passive: true });

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

  /* Non-digitized tunes only have a title (spec §5.3). */
  function renderHead(t) {
    const info = meta(t);
    const head = el("div", "tune-head");
    const top = el("div", "head-top");
    const tempo = el("span", "tempo");
    if (info.tempo) tempo.textContent = `(${titleCase(info.tempo)})`;
    const title = el("h2", "title");
    title.textContent = t.title || t.id;
    const composer = el("span", "composer");
    composer.textContent = info.composer || "";
    top.append(tempo, title, composer);
    head.appendChild(top);

    const parts = [];
    if (info.style) parts.push(escapeHtml(info.style));
    if (info.year) parts.push(escapeHtml(info.year));
    if (info.form) parts.push(escapeHtml(info.form));
    if (info.page != null) parts.push(`p. ${escapeHtml(info.page)}`);
    if (parts.length) {
      const meta_ = el("div", "meta");
      meta_.innerHTML = parts.join(" · ");
      head.appendChild(meta_);
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

    if (tune.same_chord_changes) {
      const block = detailsBlock("Same changes");
      const p = el("p");
      p.textContent = tune.same_chord_changes;
      block.appendChild(p);
      extras.appendChild(block);
    }

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

  /* -------------------------------------------------- panels & switches */

  function saveSwitch(key, on) {
    try {
      localStorage.setItem(key, on ? "1" : "0");
    } catch (e) { /* ignore */ }
  }

  function makeSwitch(label, checked, onChange) {
    const wrap = el("label", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = checked;
    input.addEventListener("change", () => onChange(input.checked));
    const knob = el("span", "knob");
    const text = el("span", "switch-label");
    text.textContent = label;
    wrap.append(input, knob, text);
    return wrap;
  }

  function scanImg(src, alt) {
    const img = el("img", "scan");
    img.src = src;
    img.alt = alt;
    img.loading = "lazy";
    img.addEventListener("click", () => openOverlay(src, alt));
    return img;
  }

  /* Show/hide panels per the switches; side-by-side handled by CSS (.dual). */
  function applyPanels(t) {
    const panels = paneEl.querySelector(".panels");
    if (!panels) return;
    const chordPanel = panels.querySelector(".panel.chords");
    const melodyPanel = panels.querySelector(".panel.melody");
    const visC = Boolean(chordPanel) && state.showChords;
    const visM = Boolean(melodyPanel) && state.showMelody;
    if (chordPanel) chordPanel.hidden = !visC;
    if (melodyPanel) melodyPanel.hidden = !visM;
    panels.classList.toggle("dual", visC && visM);
    requestAnimationFrame(fitAll);
  }

  /* ----------------------------------------------------------- main render */

  function renderTune(id) {
    const t = tuneById(id);
    if (!t) return;
    state.currentId = id;
    state.chordScan = false; // per-tune, defaults to the rendered grid
    document.title = `${t.title || id} — Grilles`;

    paneEl.innerHTML = "";
    paneEl.appendChild(renderHead(t));

    /* Switch toolbar (spec §5.4) — one switch per available asset. */
    const bar = el("div", "panel-bar");
    if (hasChordAsset(t)) {
      bar.appendChild(makeSwitch("Chords", state.showChords, (on) => {
        state.showChords = on;
        saveSwitch("grilles.showChords", on);
        applyPanels(t);
      }));
    }
    if (hasMelodyAsset(t)) {
      bar.appendChild(makeSwitch("Melody", state.showMelody, (on) => {
        state.showMelody = on;
        saveSwitch("grilles.showMelody", on);
        applyPanels(t);
      }));
    }
    if (bar.childElementCount) paneEl.appendChild(bar);

    const panels = el("div", "panels");

    if (hasChordAsset(t)) {
      const panel = el("section", "panel chords");
      const tune = meta(t);
      if (t.has_chord_json) {
        panel.classList.add("has-grid");
        const grid = el("div", "grid");
        const beats = beatsPerBar(tune);
        Object.keys(tune.sections || {}).forEach((name, i) => {
          grid.appendChild(renderSection(name, tune.sections[name], beats, i === 0, tune.time_signature));
        });
        panel.appendChild(grid);
        const extras = renderExtras(tune, beats);
        if (extras) panel.appendChild(extras);
        if (t.chord_image) {
          /* ▦ toggle: rendered grid ⇄ original scan (spec §5.4). */
          const scan = scanImg(t.chord_image, `${t.title || id} — original chord scan`);
          panel.appendChild(scan);
          const toggle = el("button", "scan-toggle");
          toggle.type = "button";
          toggle.textContent = "▦";
          toggle.title = "Show original scan";
          toggle.setAttribute("aria-label", "Toggle original scan");
          toggle.addEventListener("click", () => {
            state.chordScan = !state.chordScan;
            panel.classList.toggle("show-scan", state.chordScan);
            toggle.classList.toggle("on", state.chordScan);
            requestAnimationFrame(fitAll);
          });
          panel.appendChild(toggle);
        }
      } else {
        panel.appendChild(scanImg(t.chord_image, `${t.title || id} — chord scan`));
      }
      panels.appendChild(panel);
    }

    if (hasMelodyAsset(t)) {
      const panel = el("section", "panel melody");
      /* Digitized melody rendering (abcjs) is deferred; show the scan. */
      if (t.melody_image) {
        panel.appendChild(scanImg(t.melody_image, `${t.title || id} — melody scan`));
      }
      panels.appendChild(panel);
    }

    paneEl.appendChild(panels);
    applyPanels(t);

    viewEl.scrollTop = 0;
    if (narrowMq.matches) setListOpen(false);
    updateListHighlight();
  }

  /*
   * Shrink the grid's base font size until head + grid fit the viewport
   * (one screen page, no vertical scrolling). All grid dimensions are
   * em-based, so its height scales ~linearly with font-size.
   */
  const MIN_GRID_FONT = 8; // px — below this, scrolling beats unreadable chords

  function fitGrid() {
    const grid = paneEl.querySelector(".panel.chords:not([hidden]):not(.show-scan) .grid");
    if (!grid) return;
    grid.style.fontSize = "";
    viewEl.scrollTop = 0;
    /* Fit above the view's bottom padding, which clears the zoom buttons. */
    const padBottom = Math.max(10, parseFloat(getComputedStyle(viewEl).paddingBottom) || 0);
    for (let i = 0; i < 3; i++) {
      const viewRect = viewEl.getBoundingClientRect();
      const gridRect = grid.getBoundingClientRect();
      const bottomLimit = viewRect.top + viewEl.clientTop + viewEl.clientHeight - padBottom;
      const avail = bottomLimit - gridRect.top;
      if (gridRect.height <= avail || avail <= 0) break;
      const cur = parseFloat(getComputedStyle(grid).fontSize);
      const next = Math.max(MIN_GRID_FONT, cur * (avail / gridRect.height));
      if (cur - next < 0.5) break;
      grid.style.fontSize = next.toFixed(2) + "px";
      if (next === MIN_GRID_FONT) break;
    }
    if (state.gridZoom !== 1) {
      const fitted = parseFloat(getComputedStyle(grid).fontSize);
      grid.style.fontSize = Math.max(6, fitted * state.gridZoom).toFixed(2) + "px";
    }
  }

  function setGridZoom(zoom) {
    state.gridZoom = Math.min(2.5, Math.max(0.5, zoom));
    try {
      localStorage.setItem("grilles.gridzoom", String(state.gridZoom));
    } catch (e) { /* ignore */ }
    fitAll();
  }

  zoomInBtn.addEventListener("click", () => setGridZoom(state.gridZoom * 1.15));
  zoomOutBtn.addEventListener("click", () => setGridZoom(state.gridZoom / 1.15));

  /* Shrink chords that overflow their beat slots (spec §6.4). */
  function fitChords() {
    paneEl.querySelectorAll(".slot").forEach((slot) => {
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

  function fitAll() {
    fitGrid();
    fitChords();
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitAll, 150);
  });

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => requestAnimationFrame(fitAll));
  }

  /* ----------------------------------------------------------------- init */

  initTheme();
  try {
    /* Defaults: Chords on, Melody off (spec §5.4); user choices persist. */
    const c = localStorage.getItem("grilles.showChords");
    const m = localStorage.getItem("grilles.showMelody");
    state.showChords = c === null ? true : c === "1";
    state.showMelody = m === "1";
    const z = parseFloat(localStorage.getItem("grilles.gridzoom"));
    if (Number.isFinite(z)) state.gridZoom = Math.min(2.5, Math.max(0.5, z));
  } catch (e) { /* ignore */ }
  renderList();
  searchEl.focus();

  const initialId = decodeURIComponent(location.hash.slice(1));
  if (tuneById(initialId)) {
    renderTune(initialId);
  } else if (TUNES.length) {
    history.replaceState(null, "", "#" + encodeURIComponent(TUNES[0].id));
    renderTune(TUNES[0].id);
  } else {
    paneEl.innerHTML = '<div class="list-empty">No tunes found. Run build_data.py first.</div>';
  }
})();
