/* Grilles Displayer — search, navigation and grid rendering (spec §5, §6, §8, §9). */
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
  const imageToggle = document.getElementById("imageToggle");
  const imagePane = document.getElementById("imagePane");
  const imageEl = document.getElementById("tuneImage");
  const imageClose = document.getElementById("imageClose");
  const zoomInBtn = document.getElementById("zoomIn");
  const zoomOutBtn = document.getElementById("zoomOut");

  /* Below 700px the list is a drawer; from 900px the scan docks beside the grid. */
  const narrowMq = window.matchMedia("(max-width: 700px)");
  const wideMq = window.matchMedia("(min-width: 900px)");

  const state = {
    filtered: TUNES,
    activeIndex: -1, // keyboard highlight within filtered list
    currentId: null, // tune displayed in the main panel
    showImage: false, // original scan visible (side panel or overlay)
    imageZoom: false, // wide screens: docked scan expanded to fullscreen
    imageMag: false, // fullscreen scan magnified (pan by scrolling)
    gridZoom: 1, // user zoom factor applied on top of the fitted grid size
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

  /* ----------------------------------------------------- tune list drawer */

  function setListOpen(open) {
    document.body.classList.toggle("list-open", open);
    listToggle.setAttribute("aria-expanded", String(open));
  }

  listToggle.addEventListener("click", () => {
    setListOpen(!document.body.classList.contains("list-open"));
  });

  listBackdrop.addEventListener("click", () => setListOpen(false));

  /* -------------------------------------------------------- original scan */

  function applyImage() {
    const tune = tuneById(state.currentId);
    const src = tune && tune.image;
    const show = Boolean(src) && state.showImage;
    if (src) {
      imageToggle.hidden = false;
      imageToggle.classList.toggle("on", show);
      if (imageEl.getAttribute("src") !== src) imageEl.src = src;
      imageEl.alt = `${tune.title || tune.id} — original scan`;
    } else {
      imageToggle.hidden = true;
    }
    if (!show) {
      state.imageZoom = false;
      state.imageMag = false;
    }
    imagePane.hidden = !show;
    /* Fullscreen: always on narrow screens; on wide only after click-to-zoom. */
    const overlay = show && (!wideMq.matches || state.imageZoom);
    document.body.classList.toggle("show-image", show);
    document.body.classList.toggle("image-overlay", overlay);
    imagePane.classList.toggle("magnified", overlay && state.imageMag);
  }

  function setShowImage(show) {
    state.showImage = show;
    try {
      localStorage.setItem("grilles.image", show ? "1" : "0");
    } catch (e) { /* ignore */ }
    applyImage();
    requestAnimationFrame(fitAll);
  }

  /* Closes the fullscreen scan (back to dock on wide, hidden on narrow).
     Returns false when no overlay was open. */
  function closeImageOverlay() {
    if (!document.body.classList.contains("image-overlay")) return false;
    state.imageZoom = false;
    state.imageMag = false;
    if (wideMq.matches) applyImage();
    else setShowImage(false);
    return true;
  }

  imageToggle.addEventListener("click", () => setShowImage(!state.showImage));

  /* Click zooms the scan: dock → fullscreen (wide), fullscreen → magnified. */
  imageEl.addEventListener("click", (e) => {
    e.stopPropagation();
    if (wideMq.matches && !state.imageZoom) {
      state.imageZoom = true;
      state.imageMag = false;
    } else {
      state.imageMag = !state.imageMag;
    }
    applyImage();
    requestAnimationFrame(fitAll);
    if (state.imageMag) {
      /* Start panning from the middle of the scan. */
      requestAnimationFrame(() => {
        imagePane.scrollLeft = (imagePane.scrollWidth - imagePane.clientWidth) / 2;
        imagePane.scrollTop = (imagePane.scrollHeight - imagePane.clientHeight) / 2;
      });
    }
  });

  /* Clicking the dark backdrop (or ✕) leaves the fullscreen view. */
  imagePane.addEventListener("click", () => closeImageOverlay());
  imageClose.addEventListener("click", (e) => {
    e.stopPropagation();
    closeImageOverlay();
  });

  imageEl.addEventListener("error", () => {
    imageToggle.hidden = true;
    imagePane.hidden = true;
    document.body.classList.remove("show-image", "image-overlay");
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
    if (narrowMq.matches && searchEl.value) setListOpen(true); // show results on mobile
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
      if (closeImageOverlay()) return;
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

  /* Swipe on the tune view (mobile): right → next tune, left → previous.
     Disabled while the magnified scan is panned by touch-scrolling. */
  let swipeStart = null;

  viewEl.addEventListener("touchstart", (e) => {
    swipeStart = e.touches.length === 1 && !state.imageMag
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
    navTune(dx > 0 ? 1 : -1);
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

  /* ----------------------------------------------------------- main render */

  function renderTune(id) {
    const tune = tuneById(id);
    if (!tune) return;
    state.currentId = id;
    document.title = `${tune.title || id} — Grilles`;

    const beats = beatsPerBar(tune);
    paneEl.innerHTML = "";
    paneEl.appendChild(renderHead(tune));

    const grid = el("div", "grid");
    const sectionNames = Object.keys(tune.sections || {});
    sectionNames.forEach((name, i) => {
      grid.appendChild(renderSection(name, tune.sections[name], beats, i === 0, tune.time_signature));
    });
    paneEl.appendChild(grid);

    const extras = renderExtras(tune, beats);
    if (extras) paneEl.appendChild(extras);

    viewEl.scrollTop = 0;
    if (narrowMq.matches) setListOpen(false);
    applyImage();
    renderList(); // refresh "current" highlight
    requestAnimationFrame(fitAll);
  }

  /*
   * Shrink the grid's base font size until head + grid fit the viewport
   * (one screen page, no vertical scrolling). All grid dimensions are
   * em-based, so its height scales ~linearly with font-size.
   */
  const MIN_GRID_FONT = 8; // px — below this, scrolling beats unreadable chords

  function fitGrid() {
    const grid = paneEl.querySelector(".grid");
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
    resizeTimer = setTimeout(() => {
      applyImage(); // dock ↔ overlay when crossing the 900px breakpoint
      fitAll();
    }, 150);
  });

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => requestAnimationFrame(fitAll));
  }

  /* ----------------------------------------------------------------- init */

  initTheme();
  {
    /* Scan panel defaults to on for wide screens (docked side panel); an
       explicit user toggle is remembered. Never greet mobile with an overlay. */
    let saved = null;
    try {
      saved = localStorage.getItem("grilles.image");
    } catch (e) { /* ignore */ }
    state.showImage = (saved === null ? true : saved === "1") && wideMq.matches;
  }
  try {
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
