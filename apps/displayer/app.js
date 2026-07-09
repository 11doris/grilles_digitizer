/* Grilles Displayer — search, navigation, panels, grid rendering and
   playlists (spec §5, §6, §8, §9, §11). */
"use strict";

(function () {
  const { renderChordHTML, escapeHtml } = window.GrillesChords;
  const PL = window.GrillesPlaylists;
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
  const playlistBtn = document.getElementById("playlistBtn");
  const addPlBtn = document.getElementById("addPlBtn");
  const playlistClear = document.getElementById("playlistClear");
  const playlistMenu = document.getElementById("playlistMenu");
  const plPopover = document.getElementById("plPopover");
  const plImportFile = document.getElementById("plImportFile");

  /* Phones (narrow, or short in landscape): the list is a drawer; from 900px
     wide the visible panels sit side by side. Keep in sync with style.css. */
  const narrowMq = window.matchMedia("(max-width: 700px), (max-height: 500px)");

  const state = {
    filtered: TUNES,
    activeIndex: -1, // keyboard highlight within filtered list
    currentId: null, // tune displayed in the main panel
    showChords: true, // Chords switch (persisted)
    showMelody: false, // Melody switch (persisted)
    listCollapsed: false, // desktop: docked sidebar hidden (persisted)
    overlayMag: false, // fullscreen scan magnified (pan by scrolling)
    gridZoom: 1, // user zoom factor applied on top of the fitted grid size
    activePl: null, // active playlist id (persisted, §11.4)
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

  /* All melody sheets of a tune; melody_images exists when there are several. */
  function melodyImages(t) {
    return t.melody_images || (t.melody_image ? [t.melody_image] : []);
  }

  function activePlaylist() {
    return state.activePl ? PL.byId(state.activePl) : null;
  }

  /* Placeholder for a playlist tuneId that matches no corpus tune (the index
     changed): kept, greyed, never openable (§11.1). */
  const missingCache = Object.create(null);

  function missingEntry(id) {
    if (!missingCache[id]) {
      missingCache[id] = { id, missing: true, title: id, _hay: normalize(id) };
    }
    return missingCache[id];
  }

  /* What the sidebar lists before search: the active playlist's tunes in
     playlist order, or the whole alphabetical corpus (§11.4). */
  function baseList() {
    const pl = activePlaylist();
    if (!pl) return TUNES;
    return pl.tuneIds.map((id) => tuneById(id) || missingEntry(id));
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

  /* Desktop: ☰ collapses the docked sidebar instead (full-width tune view). */
  function setListCollapsed(collapsed) {
    state.listCollapsed = collapsed;
    document.body.classList.toggle("list-collapsed", collapsed);
    listToggle.setAttribute("aria-expanded", String(!collapsed));
    try {
      localStorage.setItem("grilles.list", collapsed ? "0" : "1");
    } catch (e) { /* ignore */ }
    requestAnimationFrame(fitAll); // pane width changed
  }

  listToggle.addEventListener("click", () => {
    if (narrowMq.matches) setListOpen(!document.body.classList.contains("list-open"));
    else setListCollapsed(!state.listCollapsed);
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

  /* Search filters within the active playlist when one is on (§11.4). */
  function filterTunes(query) {
    const list = baseList();
    const terms = normalize(query).split(/\s+/).filter(Boolean);
    if (!terms.length) return list;
    return list.filter((t) => terms.every((term) => t._hay.includes(term)));
  }

  searchEl.addEventListener("input", () => {
    state.filtered = filterTunes(searchEl.value);
    state.activeIndex = state.filtered.length ? 0 : -1;
    renderList();
    /* Searching only makes sense with the list visible — reveal it. */
    if (searchEl.value) {
      if (narrowMq.matches) setListOpen(true);
      else if (state.listCollapsed) setListCollapsed(false);
    }
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
  /* Photo/picture glyph for the per-panel "show original scan" button. */
  const ICON_IMAGE =
    '<svg viewBox="0 0 16 16" aria-hidden="true">' +
    '<rect x="1.7" y="2.7" width="12.6" height="10.6" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
    '<circle cx="5.6" cy="6.4" r="1.25"/>' +
    '<path d="M3 12.2l3.4-4 2.7 3 2-2.4 2.6 3.4z"/></svg>';

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
    /* Reorder/remove tools only in playlist mode without a search query —
       row positions within a filtered subset would be ambiguous (§11.3). */
    const tools = activePlaylist() && !searchEl.value
      ? '<span class="pl-row-tools">' +
        '<button type="button" class="pl-act" data-act="up" title="Move up">↑</button>' +
        '<button type="button" class="pl-act" data-act="down" title="Move down">↓</button>' +
        '<button type="button" class="pl-act" data-act="rm" title="Remove from playlist">✕</button>' +
        "</span>"
      : "";
    /* One string + one innerHTML: ~1,600 rows render in a few ms. */
    listEl.innerHTML = state.filtered
      .map((t, i) => {
        if (t.missing) {
          return `<div class="tune-item missing" data-id="${escapeHtml(t.id)}">` +
            '<span class="icons"><span class="icon none"></span><span class="icon none"></span></span>' +
            `<span class="txt"><span class="t">${escapeHtml(t.id)}</span>` +
            '<span class="c">not in current corpus</span></span>' +
            tools + "</div>";
        }
        const composer = meta(t).composer;
        const cls = "tune-item" +
          (t.id === state.currentId ? " current" : "") +
          (i === state.activeIndex ? " active" : "");
        return `<div class="${cls}" data-id="${escapeHtml(t.id)}">${iconCluster(t)}` +
          `<span class="txt"><span class="t">${escapeHtml(t.title || t.id)}</span>` +
          (composer ? `<span class="c">${escapeHtml(composer)}</span>` : "") +
          "</span>" + tools + "</div>";
      })
      .join("");
  }

  listEl.addEventListener("click", (e) => {
    const item = e.target.closest(".tune-item");
    if (!item) return;
    const act = e.target.closest(".pl-act");
    if (act) {
      const pl = activePlaylist();
      if (!pl) return;
      const idx = pl.tuneIds.indexOf(item.dataset.id);
      if (act.dataset.act === "rm") PL.removeTune(pl.id, item.dataset.id);
      else PL.moveTune(pl.id, idx, idx + (act.dataset.act === "up" ? -1 : 1));
      refreshList();
      updateStepButtons();
      return;
    }
    if (!item.classList.contains("missing")) openTune(item.dataset.id);
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
    /* Typing into a playlist name field (any input that isn't the search box)
       must not drive tune navigation; Escape still falls through to close. */
    if (e.target !== searchEl && e.target.matches &&
        e.target.matches("input, textarea, select") && e.key !== "Escape") {
      return;
    }
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
      if (tune && !tune.missing) openTune(tune.id);
    } else if (e.key === "Escape") {
      if (closeOverlay()) return;
      if (closePopover()) return;
      if (closeMenu()) return;
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
     filtered list order. Stops at either end (no wrap-around); playlist
     entries missing from the corpus are skipped (§11.1). */
  function navTune(delta) {
    const source = state.filtered.length ? state.filtered : baseList();
    const list = source.filter((t) => !t.missing);
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

  /* "C", "Db", "F#" → "C", "D♭", "F♯" (music accidental glyphs). */
  function noteGlyph(tonic) {
    return String(tonic).replace(/#/g, "♯").replace(/b/g, "♭");
  }

  /* {tonic, mode} → "C major" / "A♭ minor"; null when no tonic. */
  function keyLabel(k) {
    if (!k || !k.tonic) return null;
    return noteGlyph(k.tonic) + (k.mode ? " " + k.mode : "");
  }

  function harmChip(kind, label, value) {
    const chip = el("span", "harm-chip " + kind);
    if (label) {
      const l = el("span", "harm-label");
      l.textContent = label;
      chip.appendChild(l);
    }
    const v = el("span", "harm-val");
    v.textContent = value;
    chip.appendChild(v);
    return chip;
  }

  /* Key + section keys (row 1) and harmonic-fingerprint family + tags (row 2),
     from the annotated JSON (data/chords/05_annotated). Compact wrapping chip
     rows so the block stays readable on phone and desktop alike; the prose
     per-section analysis lives in the collapsible extras below the grid. */
  function renderHarmony(tune) {
    const keys = el("div", "harm-row harm-keys");
    const mainKey = keyLabel(tune.key);
    if (mainKey) keys.appendChild(harmChip("key", "Key", mainKey));

    const scorer = (tune.key_annotation || {}).scorer || {};
    const sectionKeys = scorer.section_keys || {};
    Object.keys(sectionKeys).forEach((name) => {
      const label = keyLabel(sectionKeys[name]);
      if (label) keys.appendChild(harmChip("section", displaySectionName(name), label));
    });

    const tags = el("div", "harm-row harm-tags");
    const fp = tune.harmonic_fingerprint || {};
    if (fp.family) tags.appendChild(harmChip("family", null, fp.family));
    (fp.tags || []).forEach((tag) => {
      tags.appendChild(harmChip("tag", null, tag));
    });

    if (!keys.childElementCount && !tags.childElementCount) return null;
    const wrap = el("div", "harmony");
    if (keys.childElementCount) wrap.appendChild(keys);
    if (tags.childElementCount) wrap.appendChild(tags);
    return wrap;
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

    /* Key / section keys / harmonic fingerprint (annotated tunes only). */
    const harmony = renderHarmony(info);
    if (harmony) head.appendChild(harmony);

    /* Playlist controls (§5.3, §11.2, §11.4): Prev/Next only while a playlist
       is active (set by updateStepButtons). "Add to playlist" lives in the top
       bar and acts on the current tune (see addPlBtn). */
    const actions = el("div", "head-actions");
    const prev = el("button", "step-btn step-prev");
    prev.type = "button";
    prev.textContent = "‹ Prev";
    prev.addEventListener("click", () => playlistStep(-1));
    const next = el("button", "step-btn step-next");
    next.type = "button";
    next.textContent = "Next ›";
    next.addEventListener("click", () => playlistStep(1));
    actions.append(prev, next);
    head.appendChild(actions);

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

    const fp = tune.harmonic_fingerprint;
    const modNote = ((tune.key_annotation || {}).llm || {}).modulation_note;
    if (fp && (fp.sections || modNote)) {
      const block = detailsBlock("Harmonic analysis");
      if (fp.sections && typeof fp.sections === "object") {
        const dl = el("dl", "harm-sections");
        for (const [name, desc] of Object.entries(fp.sections)) {
          const dt = el("dt");
          dt.textContent = displaySectionName(name);
          const dd = el("dd");
          dd.textContent = desc;
          dl.append(dt, dd);
        }
        block.appendChild(dl);
      }
      if (modNote) {
        const p = el("p", "harm-modnote");
        p.textContent = "Modulation: " + modNote;
        block.appendChild(p);
      }
      extras.appendChild(block);
    }

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
    const frag = document.createDocumentFragment();
    const img = el("img", "scan");
    img.src = src;
    img.alt = alt;
    /* Eager (not lazy): a scan hidden behind the ▦ toggle must already be
       loaded when first shown, or the panel briefly collapses and the page
       jumps. Only the current tune's images are ever in the DOM, so eager
       loading costs one extra small PNG per digitized tune. */
    img.addEventListener("click", () => openOverlay(src, alt));
    /* Debug aid: the crop's filename under the image. */
    const name = el("div", "scan-name");
    name.textContent = String(src).split("/").pop();
    frag.append(img, name);
    return frag;
  }

  /* Toggle on a panel that has both a rendered form and the original scan:
     swaps rendered ⇄ scan in place (per tune, defaults to rendered — §5.4).
     Sits in a tools row above the content so it never overlaps the scan; the
     icon shows what the button switches TO (photo ⇄ grid/notes). */
  function addScanToggle(panel, srcs, alt, renderedIcon) {
    panel.classList.add("has-render");
    [].concat(srcs).forEach((src) => panel.appendChild(scanImg(src, alt)));
    const tools = el("div", "panel-tools");
    const toggle = el("button", "scan-toggle");
    toggle.type = "button";
    const apply = (on) => {
      panel.classList.toggle("show-scan", on);
      toggle.classList.toggle("on", on);
      toggle.innerHTML = on ? renderedIcon : ICON_IMAGE;
      toggle.title = on ? "Show digitized version" : "Show original scan";
      toggle.setAttribute("aria-label", toggle.title);
    };
    toggle.addEventListener("click", () => {
      const scroll = viewEl.scrollTop;
      apply(!panel.classList.contains("show-scan"));
      requestAnimationFrame(fitAll); // fitGrid preserves the scroll position
      /* If a scan hasn't finished loading, the panel is briefly short and
         the browser clamps the scroll — put it back once each image is in. */
      panel.querySelectorAll("img.scan").forEach((img) => {
        if (!img.complete) {
          img.addEventListener("load", () => { viewEl.scrollTop = scroll; }, { once: true });
        }
      });
    });
    apply(false);
    tools.appendChild(toggle);
    panel.prepend(tools);
    panel._setScan = apply; // lets the ABC error fallback flip to the scan
  }

  /* Melody lead sheet from the embedded ABC (spec §5.4). Never crashes the
     page: a malformed ABC renders a warning and falls back to the scan. */
  function renderAbcSheet(panel, t) {
    const sheet = panel.querySelector(".abc-sheet");
    if (!sheet) return;
    /* abcjs styles the element it renders into inline (display:inline-block),
       which would defeat the .show-scan display:none on .abc-sheet — so give
       it an inner target and keep the wrapper ours. */
    const target = el("div");
    sheet.replaceChildren(target);
    /* Display-only: drop title/composer/origin/rhythm header lines — the app's
       tune head already shows them, and long O: lines overlap when engraved. */
    const abc = String(t.abc).replace(/^[TCOR]:.*\r?\n/gm, "");
    try {
      if (!window.ABCJS) throw new Error("abcjs not loaded");
      ABCJS.renderAbc(target, abc, {
        responsive: "resize",
        add_classes: true,
        paddingtop: 0,
        paddingbottom: 0,
      });
      if (!sheet.querySelector("svg")) throw new Error("abcjs produced no output");
    } catch (err) {
      const warn = el("div", "abc-error");
      warn.textContent = `Melody rendering failed: ${err.message}`;
      sheet.replaceChildren(warn);
      if (t.melody_image && panel._setScan) panel._setScan(true); // fall back
    }
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

  /* ------------------------------------------------------------ playlists */

  function updateTopbar() {
    const pl = activePlaylist();
    playlistBtn.textContent = pl ? pl.name : "Playlists";
    playlistBtn.classList.toggle("pl-active", Boolean(pl));
    playlistBtn.title = pl ? `Active playlist: ${pl.name}` : "Playlists";
    playlistClear.hidden = !pl;
  }

  function refreshList() {
    state.filtered = filterTunes(searchEl.value);
    if (state.activeIndex >= state.filtered.length) {
      state.activeIndex = state.filtered.length - 1;
    }
    renderList();
  }

  /* Activate (id) / deactivate (null); the displayed tune stays (§11.4). */
  function setActivePlaylist(id) {
    state.activePl = id;
    PL.setActiveId(id);
    state.activeIndex = -1;
    updateTopbar();
    refreshList();
    updateStepButtons();
  }

  playlistClear.addEventListener("click", () => setActivePlaylist(null));

  /* Step through the active playlist in order, skipping missing entries; no
     wrap-around. From a tune outside the playlist, Next opens the first. */
  function playlistStep(delta) {
    const pl = activePlaylist();
    if (!pl) return;
    const seq = pl.tuneIds.filter((id) => tuneById(id));
    if (!seq.length) return;
    const idx = seq.indexOf(state.currentId);
    const target = idx === -1 ? (delta > 0 ? seq[0] : null) : seq[idx + delta];
    if (target) openTune(target);
  }

  function updateStepButtons() {
    const prev = paneEl.querySelector(".step-prev");
    const next = paneEl.querySelector(".step-next");
    if (!prev || !next) return;
    const pl = activePlaylist();
    prev.hidden = next.hidden = !pl;
    if (!pl) return;
    const seq = pl.tuneIds.filter((id) => tuneById(id));
    const idx = seq.indexOf(state.currentId);
    prev.disabled = idx <= 0;
    next.disabled = idx === -1 ? !seq.length : idx >= seq.length - 1;
  }

  /* Dropdown under its anchor on desktop; a bottom sheet on phones (§11.6). */
  function positionDropdown(elm, anchor) {
    if (narrowMq.matches) {
      elm.classList.add("sheet");
      elm.style.top = elm.style.left = "";
      return;
    }
    elm.classList.remove("sheet");
    const r = anchor.getBoundingClientRect();
    elm.style.top = r.bottom + 6 + "px";
    const w = elm.offsetWidth;
    elm.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + "px";
  }

  /* --- Playlists menu (top bar): activate, rename, delete, export/import. */

  function closeMenu() {
    if (playlistMenu.hidden) return false;
    playlistMenu.hidden = true;
    return true;
  }

  function openMenu() {
    closePopover();
    renderMenu();
    playlistMenu.hidden = false;
    positionDropdown(playlistMenu, playlistBtn);
  }

  function renderMenu() {
    const lists = PL.all();
    let html = '<div class="pl-menu-title">Playlists</div>';
    if (state.activePl) {
      html += '<button type="button" class="pl-all" data-act="all">Show all tunes</button>';
    }
    if (!lists.length) {
      html += '<div class="pl-menu-empty">No playlists yet — use “＋ Add to playlist” on a tune, or create one here.</div>';
    }
    html += lists
      .map((p) => {
        const active = p.id === state.activePl;
        return `<div class="pl-menu-row${active ? " active" : ""}" data-id="${escapeHtml(p.id)}">` +
          `<button type="button" class="pl-name" data-act="activate" title="${active ? "Deactivate" : "Activate"}">` +
          `${escapeHtml(p.name)} <span class="pl-count">${p.tuneIds.length}</span></button>` +
          '<button type="button" class="pl-tool" data-act="rename" title="Rename">✎</button>' +
          '<button type="button" class="pl-tool" data-act="delete" title="Delete">🗑</button></div>';
      })
      .join("");
    html += '<div class="pl-menu-footer">' +
      '<button type="button" data-act="new">＋ New playlist</button>' +
      '<button type="button" data-act="export">Export</button>' +
      '<button type="button" data-act="import">Import</button></div>' +
      '<div class="pl-status" id="plStatus"></div>';
    playlistMenu.innerHTML = html;
  }

  /* Swap a row's name (or the "new" button) for an inline name input. */
  function startRename(row, id) {
    const p = PL.byId(id);
    const nameBtn = row.querySelector(".pl-name");
    if (!p || !nameBtn) return;
    const input = el("input", "pl-name-input");
    input.value = p.name;
    row.replaceChild(input, nameBtn);
    input.focus();
    input.select();
    const commit = () => {
      const v = input.value.trim();
      if (v && v !== p.name) {
        PL.rename(id, v);
        updateTopbar();
      }
      renderMenu();
    };
    input.addEventListener("keydown", (ev) => {
      ev.stopPropagation(); // Escape cancels the edit only, not the menu
      if (ev.key === "Enter") commit();
      else if (ev.key === "Escape") renderMenu();
    });
    /* Enter/Escape re-render the menu, detaching the input — the blur that
       follows must not commit again (or commit a canceled edit). */
    input.addEventListener("blur", () => {
      if (input.isConnected) commit();
    });
  }

  function startNewPlaylist(btn, tuneId, rerender) {
    const input = el("input", "pl-name-input");
    input.placeholder = "New playlist name";
    btn.replaceWith(input);
    input.focus();
    input.addEventListener("keydown", (ev) => {
      ev.stopPropagation(); // Escape cancels the field only, not the panel
      if (ev.key === "Enter") {
        const v = input.value.trim();
        if (!v) return;
        /* Creation does not activate the playlist (§11.2). */
        PL.create(v, tuneId ? [tuneId] : []);
        rerender();
      } else if (ev.key === "Escape") {
        rerender();
      }
    });
    input.addEventListener("blur", () => {
      if (input.isConnected) rerender();
    });
  }

  playlistMenu.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const row = e.target.closest(".pl-menu-row");
    const id = row ? row.dataset.id : null;
    const act = btn.dataset.act;
    if (act === "all") {
      setActivePlaylist(null);
      closeMenu();
    } else if (act === "activate" && id) {
      setActivePlaylist(id === state.activePl ? null : id);
      closeMenu();
    } else if (act === "rename" && id) {
      startRename(row, id);
    } else if (act === "delete" && id) {
      const p = PL.byId(id);
      if (p && window.confirm(`Delete playlist “${p.name}”?`)) {
        if (id === state.activePl) setActivePlaylist(null);
        PL.remove(id);
        renderMenu();
      }
    } else if (act === "new") {
      startNewPlaylist(btn, null, renderMenu);
    } else if (act === "export") {
      exportPlaylists();
    } else if (act === "import") {
      plImportFile.click();
    }
  });

  playlistBtn.addEventListener("click", (e) => {
    e.stopPropagation(); // keep the outside-click closer from firing
    if (playlistMenu.hidden) openMenu();
    else closeMenu();
  });

  /* Top-bar "＋": add the currently displayed tune to a playlist (§11.2). The
     popover drops from the button on desktop, becomes a bottom sheet on phones. */
  addPlBtn.addEventListener("click", (e) => {
    e.stopPropagation(); // keep the outside-click closer from firing
    const t = tuneById(state.currentId);
    if (!t) return;
    if (plPopover.hidden) openPopover(addPlBtn, t);
    else closePopover();
  });

  /* --- Export / import (§11.5). */

  function exportPlaylists() {
    const blob = new Blob([PL.exportJson()], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "grilles-playlists.json";
    /* The synthetic click must not bubble to the outside-click closer. */
    a.addEventListener("click", (e) => e.stopPropagation());
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  }

  plImportFile.addEventListener("change", () => {
    const file = plImportFile.files && plImportFile.files[0];
    plImportFile.value = ""; // allow re-picking the same file
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const res = PL.importJson(String(reader.result));
      renderMenu();
      const status = document.getElementById("plStatus");
      if (status) {
        status.textContent = res.error
          ? `Import failed: ${res.error} — existing playlists unchanged.`
          : `Imported ${res.added} playlist${res.added === 1 ? "" : "s"}.`;
        status.classList.toggle("error", Boolean(res.error));
      }
    };
    reader.readAsText(file);
  });

  /* --- Add-to-playlist popover, anchored to the tune-head button (§11.2). */

  let popoverTuneId = null;

  function closePopover() {
    if (plPopover.hidden) return false;
    plPopover.hidden = true;
    popoverTuneId = null;
    return true;
  }

  function openPopover(anchor, t) {
    closeMenu();
    popoverTuneId = t.id;
    renderPopover();
    plPopover.hidden = false;
    positionDropdown(plPopover, anchor);
  }

  function renderPopover() {
    const lists = PL.all();
    let html = '<div class="pl-menu-title">Add to playlist</div>';
    html += lists
      .map((p) => {
        const checked = p.tuneIds.includes(popoverTuneId);
        return `<label class="pl-check"><input type="checkbox" data-pl="${escapeHtml(p.id)}"${checked ? " checked" : ""}>` +
          `<span class="pl-check-name">${escapeHtml(p.name)}</span>` +
          `<span class="pl-count">${p.tuneIds.length}</span></label>`;
      })
      .join("");
    html += '<div class="pl-menu-footer"><button type="button" data-act="new">＋ New playlist…</button></div>';
    plPopover.innerHTML = html;
  }

  /* Checkbox = membership of the CURRENT tune; toggling persists immediately
     and the popover stays open, so several playlists can be ticked in a row. */
  plPopover.addEventListener("change", (e) => {
    const cb = e.target;
    if (cb.type !== "checkbox" || !popoverTuneId) return;
    if (cb.checked) PL.addTune(cb.dataset.pl, popoverTuneId);
    else PL.removeTune(cb.dataset.pl, popoverTuneId);
    const label = cb.closest(".pl-check");
    const count = label && label.querySelector(".pl-count");
    const p = PL.byId(cb.dataset.pl);
    if (count && p) count.textContent = p.tuneIds.length;
    /* Membership changes can affect the active playlist's sidebar. */
    refreshList();
    updateStepButtons();
  });

  plPopover.addEventListener("click", (e) => {
    const btn = e.target.closest('button[data-act="new"]');
    if (btn) startNewPlaylist(btn, popoverTuneId, renderPopover);
  });

  /* Click outside closes menu and popover (Escape is handled with the keys). */
  document.addEventListener("click", (e) => {
    /* A click that swapped its own target out of the DOM (inline name inputs
       replacing their button) was inside a panel — don't treat it as outside. */
    if (!e.target.isConnected) return;
    if (!plPopover.hidden && !plPopover.contains(e.target)) closePopover();
    if (!playlistMenu.hidden && !playlistMenu.contains(e.target) &&
        !playlistBtn.contains(e.target)) {
      closeMenu();
    }
  });

  /* ----------------------------------------------------------- main render */

  function renderTune(id) {
    const t = tuneById(id);
    if (!t) return;
    state.currentId = id;
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
        const grid = el("div", "grid");
        const beats = beatsPerBar(tune);
        Object.keys(tune.sections || {}).forEach((name, i) => {
          grid.appendChild(renderSection(name, tune.sections[name], beats, i === 0, tune.time_signature));
        });
        panel.appendChild(grid);
        const extras = renderExtras(tune, beats);
        if (extras) panel.appendChild(extras);
        if (t.chord_image) {
          addScanToggle(panel, t.chord_image, `${t.title || id} — original chord scan`, ICON_GRID);
        } else {
          panel.classList.add("has-render");
        }
      } else {
        panel.appendChild(scanImg(t.chord_image, `${t.title || id} — chord scan`));
      }
      panels.appendChild(panel);
    }

    let melodyPanel = null;
    if (hasMelodyAsset(t)) {
      const panel = el("section", "panel melody");
      const scans = melodyImages(t);
      if (t.has_melody_abc && t.abc) {
        panel.appendChild(el("div", "abc-sheet"));
        if (scans.length) {
          addScanToggle(panel, scans, `${t.title || id} — original melody scan`, ICON_NOTES);
        } else {
          panel.classList.add("has-render");
        }
        melodyPanel = panel;
      } else {
        scans.forEach((src) => panel.appendChild(scanImg(src, `${t.title || id} — melody scan`)));
      }
      panels.appendChild(panel);
    }

    paneEl.appendChild(panels);
    /* abcjs measures the container, so render after the panel is in the DOM. */
    if (melodyPanel) renderAbcSheet(melodyPanel, t);
    applyPanels(t);

    viewEl.scrollTop = 0;
    if (narrowMq.matches) setListOpen(false);
    updateListHighlight();
    updateStepButtons();
    closePopover(); // an open add-popover belongs to the previous tune
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
    /* Measure from the top, but give the user their scroll position back —
       re-fits triggered by panel toggles must not jump the page. */
    const prevScroll = viewEl.scrollTop;
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
    viewEl.scrollTop = prevScroll;
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
    if (localStorage.getItem("grilles.list") === "0") setListCollapsed(true);
  } catch (e) { /* ignore */ }
  /* Restore the active playlist (§11.4); a stale id starts deactivated. */
  const storedPl = PL.getActiveId();
  if (storedPl && PL.byId(storedPl)) state.activePl = storedPl;
  updateTopbar();
  state.filtered = filterTunes("");
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
