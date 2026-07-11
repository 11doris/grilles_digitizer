/* Grilles Displayer — search, navigation, panels, grid rendering and
   playlists (spec §5, §6, §8, §9, §11). */
"use strict";

(function () {
  const { renderChordHTML, escapeHtml, transposeChordSymbol, pitchClass,
    chordDegree, FLAT_SPELL, SHARP_SPELL } = window.GrillesChords;
  const PL = window.GrillesPlaylists;
  const TUNES = window.TUNES || [];
  /* Per-tune similarity suggestions (spec §8), bundled by build_data.py from
     data/chords/06_similarity/displayer_similar.json; empty when the engine
     hasn't run. Only digitized tunes appear as suggestions by construction. */
  const SIMILAR = window.SIMILAR || {};

  /* Target tonics offered by the transpose control, in pitch-class order, with
     the book's preferred enharmonic spelling per mode (spec §7.2): "Gb major"
     not "F# major", "Ebm" not "D#m". */
  const MAJOR_TONICS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const MINOR_TONICS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"];
  // Pitch classes whose key uses a sharp-biased chord spelling (rest use flats).
  const MAJOR_SHARP_PCS = new Set([2, 4, 7, 9, 11]); // D E G A B
  const MINOR_SHARP_PCS = new Set([1, 4, 6, 8, 11]); // C#m Em F#m G#m Bm

  function spellTableFor(pc, mode) {
    const sharp = mode === "minor" ? MINOR_SHARP_PCS.has(pc) : MAJOR_SHARP_PCS.has(pc);
    return sharp ? SHARP_SPELL : FLAT_SPELL;
  }

  /* Render a chord symbol, applying the current transposition if one is set. */
  function renderChord(sym) {
    const tr = state.transpose;
    return renderChordHTML(tr ? transposeChordSymbol(sym, tr.shift, tr.spell) : sym);
  }

  const searchEl = document.getElementById("search");
  const chordFilterBtn = document.getElementById("chordFilter");
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

  /* Phones (narrow, or short in landscape) and portrait tablets up to the 900px
     desktop seam get the mobile treatment: the list is a drawer; from 900px wide
     the visible panels sit side by side. Keep in sync with style.css. */
  const narrowMq = window.matchMedia(
    "(max-width: 700px), (max-height: 500px), (max-width: 899px) and (orientation: portrait)");

  const state = {
    filtered: TUNES,
    activeIndex: -1, // keyboard highlight within filtered list
    currentId: null, // tune displayed in the main panel
    showChords: true, // Chords switch (persisted)
    showMelody: true, // Melody switch (persisted); default on when a melody exists
    showVerses: true, // Verses switch (persisted); hides verse_* sections from the grid
    listCollapsed: false, // desktop: docked sidebar hidden (persisted)
    overlayMag: false, // fullscreen scan magnified (pan by scrolling)
    gridZoom: 1, // user zoom factor on top of the fitted grid size; reset per tune
    activePl: null, // active playlist id (persisted, §11.4)
    transpose: null, // {shift, spell, targetPc, mode} or null (original key); per-tune
    activeVariants: new Set(), // indices of variants swapped into the grid (independent, but exclusive among variants sharing a bar); per-tune
    chordView: "grid", // chords panel: "grid" (4 bars/row) | "boxes" (book layout) | "scan" (persisted)
    boxTint: true, // book layout: section shading on/off (persisted)
    chordsOnly: false, // list filter: only tunes with digitized chords (persisted)
    startsOn: "", // list filter: opening degree ("" = any, "unknown", "other", or a degree; §8.2a)
    keyFilter: "", // list filter: annotated key ("" = any, "unknown", or "F major" etc.; §8.2b)
    formFilter: "", // list filter: fingerprint family ("" = any, "unknown", "other", or a family; §8.2b)
    tagFilter: new Set(), // list filter: fingerprint tags — a tune must carry every checked tag (§8.2b)
    showSuggest: null, // open suggestions group: null | "tunes" | "sections"; per-tune
    compare: null, // {otherId, mode: original|transposed|roman, bars} — comparison view (§8.3); per-tune
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

  /* Verse sections (spec §6.2 naming: "verse", "verse_A", …) are auxiliary — the
     Verses switch hides them so only the chorus grid shows. */
  const VERSE_SECTION = /^verse/i;

  function hasVerseSection(t) {
    return Object.keys(meta(t).sections || {}).some((n) => VERSE_SECTION.test(n));
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

  /* Section-shading toggle (topbar, next to the theme button): tints every
     section's boxes/bars in both chord views; body.tint-off turns it off. */
  const tintBtn = document.getElementById("tintToggle");

  function applyTintToggle() {
    document.body.classList.toggle("tint-off", !state.boxTint);
    tintBtn.classList.toggle("on", state.boxTint);
    tintBtn.setAttribute("aria-pressed", String(state.boxTint));
    tintBtn.title = state.boxTint ? "Hide section shading" : "Show section shading";
    tintBtn.setAttribute("aria-label", tintBtn.title);
  }

  tintBtn.addEventListener("click", () => {
    state.boxTint = !state.boxTint;
    try {
      localStorage.setItem("grilles.boxTint", state.boxTint ? "1" : "0");
    } catch (e) { /* ignore */ }
    applyTintToggle();
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

  /* Search filters within the active playlist when one is on (§11.4). The
     "digitized chords" toggle further restricts the list to tunes carrying
     chord JSON, independently of the search query. */
  function filterTunes(query) {
    let list = baseList();
    const terms = normalize(query).split(/\s+/).filter(Boolean);
    if (terms.length) list = list.filter((t) => terms.every((term) => t._hay.includes(term)));
    if (state.chordsOnly) list = list.filter((t) => t.has_chord_json);
    /* "Starts on" filter (§8.2a) — over `opening.degree` from the annotation;
       tunes without one land in the "unknown" bucket rather than vanishing. */
    if (state.startsOn) {
      list = list.filter((t) => {
        const deg = ((meta(t).opening || {}).degree) || null;
        if (state.startsOn === "unknown") return !deg;
        if (state.startsOn === "other") return deg !== null && rareDegrees.has(deg);
        return deg === state.startsOn;
      });
    }
    /* Harmonic filters (§8.2b) — over the key annotation and the fingerprint;
       tunes without one land in the "unknown" bucket rather than vanishing. */
    if (state.keyFilter) {
      list = list.filter((t) => {
        const label = keyLabelOf(t);
        return state.keyFilter === "unknown" ? !label : label === state.keyFilter;
      });
    }
    if (state.formFilter) {
      list = list.filter((t) => {
        const fam = familyOf(t);
        if (state.formFilter === "unknown") return !fam;
        if (state.formFilter === "other") return fam !== null && rareFamilies.has(fam);
        return fam === state.formFilter;
      });
    }
    if (state.tagFilter.size) {
      list = list.filter((t) => {
        const tags = (meta(t).harmonic_fingerprint || {}).tags || [];
        return [...state.tagFilter].every((tag) => tags.includes(tag));
      });
    }
    return list;
  }

  /* Re-filter after a topbar filter changed; reveal the narrowed list like
     the digitized-chords toggle does (drawer on phones, un-collapse on desktop). */
  function refilterList(reveal) {
    state.filtered = filterTunes(searchEl.value);
    state.activeIndex = state.filtered.length ? 0 : -1;
    renderList();
    if (reveal) {
      if (narrowMq.matches) setListOpen(true);
      else if (state.listCollapsed) setListCollapsed(false);
    }
  }

  /* --------------------------------------------- "starts on" filter (§8.2a) */

  const startsOnEl = document.getElementById("startsOnFilter");
  /* Canonical display order for opening degrees (pitch order, upper before
     lower); anything not listed sorts after, alphabetically. */
  const DEGREE_ORDER = ["I", "i", "bII", "bii", "II", "ii", "bIII", "biii",
    "III", "iii", "IV", "iv", "#IV", "#iv", "V", "v", "bVI", "bvi",
    "VI", "vi", "bVII", "bvii", "VII", "vii"];
  let rareDegrees = new Set();

  /* Populate the dropdown from the degrees actually present in the bundled
     data (spec: don't hard-code the list). Degrees carried by a single tune
     collapse into an "other" bucket to keep the dropdown short. */
  function initStartsOnFilter() {
    if (!startsOnEl) return;
    const counts = new Map();
    let unknown = 0;
    TUNES.forEach((t) => {
      const deg = ((meta(t).opening || {}).degree) || null;
      if (deg) counts.set(deg, (counts.get(deg) || 0) + 1);
      else unknown++;
    });
    if (!counts.size) { // no annotated tunes: hide the control entirely
      startsOnEl.hidden = true;
      return;
    }
    const degrees = [...counts.keys()].sort((a, b) => {
      const ia = DEGREE_ORDER.indexOf(a), ib = DEGREE_ORDER.indexOf(b);
      if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
      return a.localeCompare(b);
    });
    rareDegrees = new Set(degrees.filter((d) => counts.get(d) < 2));
    const opts = ['<option value="">Starts on…</option>'];
    degrees.filter((d) => !rareDegrees.has(d)).forEach((d) => {
      opts.push(`<option value="${escapeHtml(d)}">starts on ${escapeHtml(d)} (${counts.get(d)})</option>`);
    });
    if (rareDegrees.size) {
      const n = [...rareDegrees].reduce((s, d) => s + counts.get(d), 0);
      opts.push(`<option value="other">other (${n})</option>`);
    }
    if (unknown) opts.push(`<option value="unknown">unknown (${unknown})</option>`);
    startsOnEl.innerHTML = opts.join("");
    startsOnEl.addEventListener("change", () => {
      state.startsOn = startsOnEl.value;
      startsOnEl.classList.toggle("on", Boolean(state.startsOn));
      state.filtered = filterTunes(searchEl.value);
      state.activeIndex = state.filtered.length ? 0 : -1;
      renderList();
      /* Reveal the narrowed list, like the digitized-chords toggle. */
      if (state.startsOn) {
        if (narrowMq.matches) setListOpen(true);
        else if (state.listCollapsed) setListCollapsed(false);
      }
    });
  }

  /* ------------------------------------- key / form / tag filters (§8.2b) */

  const keyFilterEl = document.getElementById("keyFilter");
  const formFilterEl = document.getElementById("formFilter");
  const tagFilterBtn = document.getElementById("tagFilterBtn");
  const tagFilterMenu = document.getElementById("tagFilterMenu");

  /* "F major" from the key annotation, or null when the tune has none. */
  function keyLabelOf(t) {
    const k = meta(t).key;
    return k && k.tonic ? k.tonic + " " + (k.mode || "major") : null;
  }

  function familyOf(t) {
    return (meta(t).harmonic_fingerprint || {}).family || null;
  }

  let rareFamilies = new Set();

  /* Keys sorted chromatically (majors interleaved with minors per tonic). */
  const TONIC_ORDER = ["C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb",
    "G", "G#", "Ab", "A", "A#", "Bb", "B"];

  function initKeyFilter() {
    if (!keyFilterEl) return;
    const counts = new Map();
    let unknown = 0;
    TUNES.forEach((t) => {
      const label = keyLabelOf(t);
      if (label) counts.set(label, (counts.get(label) || 0) + 1);
      else unknown++;
    });
    if (!counts.size) {
      keyFilterEl.hidden = true;
      return;
    }
    const labels = [...counts.keys()].sort((a, b) => {
      const ta = TONIC_ORDER.indexOf(a.split(" ")[0]);
      const tb = TONIC_ORDER.indexOf(b.split(" ")[0]);
      if (ta !== tb) return ta - tb;
      return a.localeCompare(b); // major before minor on the same tonic
    });
    const opts = ['<option value="">Key…</option>'];
    labels.forEach((l) => {
      opts.push(`<option value="${escapeHtml(l)}">${escapeHtml(l)} (${counts.get(l)})</option>`);
    });
    if (unknown) opts.push(`<option value="unknown">unknown (${unknown})</option>`);
    keyFilterEl.innerHTML = opts.join("");
    keyFilterEl.addEventListener("change", () => {
      state.keyFilter = keyFilterEl.value;
      keyFilterEl.classList.toggle("on", Boolean(state.keyFilter));
      refilterList(Boolean(state.keyFilter));
    });
  }

  /* Fingerprint families are free text with a long tail — families carried by
     a single tune collapse into an "other" bucket to keep the dropdown short. */
  function initFormFilter() {
    if (!formFilterEl) return;
    const counts = new Map();
    let unknown = 0;
    TUNES.forEach((t) => {
      const fam = familyOf(t);
      if (fam) counts.set(fam, (counts.get(fam) || 0) + 1);
      else unknown++;
    });
    if (!counts.size) {
      formFilterEl.hidden = true;
      return;
    }
    const families = [...counts.keys()].sort((a, b) =>
      counts.get(b) - counts.get(a) || a.localeCompare(b));
    rareFamilies = new Set(families.filter((f) => counts.get(f) < 2));
    const opts = ['<option value="">Form…</option>'];
    families.filter((f) => !rareFamilies.has(f)).forEach((f) => {
      opts.push(`<option value="${escapeHtml(f)}">${escapeHtml(f)} (${counts.get(f)})</option>`);
    });
    if (rareFamilies.size) {
      const n = [...rareFamilies].reduce((s, f) => s + counts.get(f), 0);
      opts.push(`<option value="other">other (${n})</option>`);
    }
    if (unknown) opts.push(`<option value="unknown">unknown (${unknown})</option>`);
    formFilterEl.innerHTML = opts.join("");
    formFilterEl.addEventListener("change", () => {
      state.formFilter = formFilterEl.value;
      formFilterEl.classList.toggle("on", Boolean(state.formFilter));
      refilterList(Boolean(state.formFilter));
    });
  }

  /* Tag filter: a dropdown of checkboxes (multi-check). Checked tags combine
     with AND — each additional check narrows the list further. */
  let tagCounts = [];

  function tagFilterLabel() {
    tagFilterBtn.textContent = state.tagFilter.size ? `Tags · ${state.tagFilter.size}` : "Tags";
    tagFilterBtn.classList.toggle("on", state.tagFilter.size > 0);
  }

  function renderTagMenu() {
    tagFilterMenu.innerHTML =
      '<div class="tag-hint">Show tunes carrying every checked tag</div>' +
      tagCounts.map(([tag, count]) =>
        `<label class="tag-row"><input type="checkbox" value="${escapeHtml(tag)}"` +
        (state.tagFilter.has(tag) ? " checked" : "") +
        `><span class="tag-name">${escapeHtml(tag)}</span>` +
        `<span class="tag-count">${count}</span></label>`).join("");
  }

  function closeTagMenu() {
    if (tagFilterMenu.hidden) return false;
    tagFilterMenu.hidden = true;
    tagFilterBtn.setAttribute("aria-expanded", "false");
    return true;
  }

  function initTagFilter() {
    if (!tagFilterBtn || !tagFilterMenu) return;
    const counts = new Map();
    TUNES.forEach((t) => {
      ((meta(t).harmonic_fingerprint || {}).tags || []).forEach((tag) => {
        counts.set(tag, (counts.get(tag) || 0) + 1);
      });
    });
    if (!counts.size) return; // button stays hidden
    tagCounts = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    tagFilterBtn.hidden = false;
    tagFilterBtn.addEventListener("click", (e) => {
      e.stopPropagation(); // keep the outside-click closer from firing
      if (tagFilterMenu.hidden) {
        renderTagMenu();
        tagFilterMenu.hidden = false;
        tagFilterBtn.setAttribute("aria-expanded", "true");
        positionDropdown(tagFilterMenu, tagFilterBtn);
      } else {
        closeTagMenu();
      }
    });
    /* Checking keeps the menu open so several tags can be combined. */
    tagFilterMenu.addEventListener("change", (e) => {
      const cb = e.target.closest('input[type="checkbox"]');
      if (!cb) return;
      if (cb.checked) state.tagFilter.add(cb.value);
      else state.tagFilter.delete(cb.value);
      tagFilterLabel();
      refilterList(state.tagFilter.size > 0);
    });
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

  /* Reflect the digitized-chords filter on the button and re-filter the list. */
  function applyChordFilter() {
    chordFilterBtn.classList.toggle("on", state.chordsOnly);
    chordFilterBtn.setAttribute("aria-pressed", String(state.chordsOnly));
    state.filtered = filterTunes(searchEl.value);
    state.activeIndex = state.filtered.length ? 0 : -1;
    renderList();
  }

  chordFilterBtn.addEventListener("click", () => {
    state.chordsOnly = !state.chordsOnly;
    try {
      localStorage.setItem("grilles.chordsOnly", state.chordsOnly ? "1" : "0");
    } catch (e) { /* ignore */ }
    applyChordFilter();
    /* Reveal the list so the narrowed result is visible on phones/collapsed. */
    if (narrowMq.matches) setListOpen(true);
    else if (state.listCollapsed) setListCollapsed(false);
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
  /* Dense 4×2 lattice for the "book layout" view (8 boxes per row, like the
     printed grille). */
  const ICON_BOXES =
    '<svg viewBox="0 0 16 16" aria-hidden="true">' +
    '<path d="M1.2 4.4h13.6M1.2 8h13.6M1.2 11.6h13.6M1.2 4.4v7.2M4.6 4.4v7.2M8 4.4v7.2M11.4 4.4v7.2M14.8 4.4v7.2" ' +
    'fill="none" stroke="currentColor" stroke-width="1.1"/></svg>';

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
      if (closeTagMenu()) return;
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

  /* A bar is a fixed grid of `beats` equal columns (one per beat). Each chord is
     anchored to its beat's column line and left-aligned there, spanning to the
     next chord's beat, so a beat sits at the same fraction of every bar's width
     regardless of the chord's own width — beat 3 of one bar lines up vertically
     with beat 3 of the bar below it (spec §6.1). A chord wider than the room its
     span allows is kept from colliding with the next by fitGridWidth, which
     measures crowding per chord-span (chord width vs its span's columns), not per
     bar. `data-beats`/`data-span` hand those measurements to the fit pass. */
  function fillBar(cell, barObj, beats, chordRenderer) {
    const rc = chordRenderer || renderChord;
    const entries = Object.entries(barObj.beats || {})
      .map(([k, v]) => [parseInt(k, 10), v])
      .filter(([k]) => Number.isFinite(k) && k >= 1 && k <= beats)
      .sort((a, b) => a[0] - b[0]);
    // Crowded bars (3+ chords) get the condensed treatment (.tight, see CSS) so
    // one dense bar doesn't shrink the whole grid's font — fitGridWidth measures
    // the already-condensed widths.
    if (entries.length >= 3) cell.classList.add("tight");
    cell.style.gridTemplateColumns = `repeat(${beats}, 1fr)`;
    cell.dataset.beats = String(beats);
    entries.forEach(([beat, chord], idx) => {
      const next = idx + 1 < entries.length ? entries[idx + 1][0] : beats + 1;
      const slot = el("div", "slot");
      slot.style.gridColumn = `${beat} / ${next}`;
      slot.dataset.span = String(next - beat);
      slot.innerHTML = rc(chord);
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
          // An active variant swaps its beats into the matching bar (same bar
          // number, so barlines/section marks are unchanged) and flags it.
          const ov = opts && opts.overrides && opts.overrides[idx];
          if (ov) cell.classList.add("variant-swap");
          fillBar(cell, ov ? { bar: bar.bar, beats: ov.beats } : bar, beats,
            opts && opts.renderChord);
        } else {
          cell.classList.add("empty");
        }
        row.appendChild(cell);
      }
      frag.appendChild(row);
    }
    return frag;
  }

  function renderSection(name, bars, beats, isFirst, ts, overrides, renderer) {
    const sec = el("div", "section");
    sec.style.setProperty("--bxhue", sectionTint(name)); // section shading
    const badge = el("div", "sec-label");
    badge.textContent = displaySectionName(name);
    sec.appendChild(badge);
    sec.appendChild(renderGrid(bars, beats,
      { double: true, timesig: isFirst ? ts : null, overrides, renderChord: renderer }));
    return sec;
  }

  /* --------------------------------------- book layout ("boxes") rendering */

  /* Re-creation of the printed grille (data/chords/01_crops): one contiguous
     lattice of boxes, a section per row of (up to) 8 boxes, the section letter
     in the left margin and the form badge top-right. Unlike the book we write
     the chords into every box instead of the "—" repeat dashes. */
  const BOXES_PER_ROW = 8;

  /* Split a section's bars into lattice rows. The book right-aligns a trailing
     partial row under the last columns (I Got Rhythm's A' bars 9–10 sit under
     columns 7–8; Au Privave's bars 9–12 under 5–8); a section that fits one row
     starts at column 1. */
  function boxRowsOf(name, bars) {
    const rows = [];
    for (let i = 0; i < bars.length; i += BOXES_PER_ROW) {
      const slice = bars.slice(i, i + BOXES_PER_ROW);
      const trailing = i > 0 && slice.length < BOXES_PER_ROW;
      rows.push({
        section: i === 0 ? name : null,
        start: trailing ? BOXES_PER_ROW - slice.length + 1 : 1,
        bars: slice,
      });
    }
    if (!rows.length) rows.push({ section: name, start: 1, bars: [] });
    return rows;
  }

  /* Section tints: subtle shades, identical for repeats of a section (A, A1,
     A2 share one tint) and CONSISTENT ACROSS TUNES — the common letters map to
     fixed hues; anything else (verse_A, interlude, …) hashes into a fixed pool
     so the same name always lands on the same shade everywhere. */
  const TINT_TABLE = {
    A: "#5b8dd6", B: "#d9a441", C: "#5fae7d", D: "#a97fd1",
    E: "#d97b7b", F: "#5bbcd6",
  };
  const TINT_POOL = ["#8a8fa3", "#b08a5e", "#7da3a0", "#a3869a", "#96a36b", "#7f8fc4"];

  /* Returns the section's hue; the CSS mixes it into the theme background
     (--bxtint), stronger in dark mode where a light wash wouldn't show. */
  function sectionTint(name) {
    const key = String(name || "").replace(/\d+$/, ""); // A1 → A, verse_A1 → verse_A
    const hash = [...key].reduce((s, c) => (s * 31 + c.charCodeAt(0)) >>> 0, 0);
    return TINT_TABLE[key] || TINT_POOL[hash % TINT_POOL.length];
  }

  /* One box, following the book's conventions:
     - one chord: big, centred;
     - two chords on the bar's two halves (beats 1 and 3 in 4/4): the diagonal
       split — top-right ↔ bottom-left line, first chord in the top-left
       triangle, second in the bottom-right one;
     - two chords of uneven length (1+4, 1+2, …): the long chord big, the
       short one in a small framed inset box in the corner matching its
       position (bottom-right when late, top-left for a pickup chord);
     - three or more: the box halves horizontally — top strip = the bar's
       first half, bottom strip = the second — and a half with several chords
       splits into side-by-side cells (Eb over Fm7|F#o for beats 1, 3, 4;
       four chords make the 2×2 quadrants).
     Positions encode the beats, so a small superscript digit only marks a
     chord that sits off its position's implied beat. */
  function fillBox(cell, barObj, beats) {
    const entries = Object.entries(barObj.beats || {})
      .map(([k, v]) => [parseInt(k, 10), v])
      .filter(([k]) => Number.isFinite(k) && k >= 1 && k <= beats)
      .sort((a, b) => a[0] - b[0]);
    const chordHtml = ([beat, chord], expected) =>
      (beat !== expected ? `<sup class="bx-beat">${beat}</sup>` : "") +
      renderChord(chord);
    if (!entries.length) return;
    const mid = Math.floor(beats / 2) + 1; // first beat of the bar's second half
    if (entries.length === 1) {
      const solo = el("div", "bx-solo");
      solo.innerHTML = chordHtml(entries[0], 1);
      cell.appendChild(solo);
    } else if (entries.length === 2 && entries[0][0] === 1 && entries[1][0] === mid) {
      cell.classList.add("duo", "tight");
      const a = el("div", "bx-a");
      a.innerHTML = chordHtml(entries[0], 1);
      const b = el("div", "bx-b");
      b.innerHTML = chordHtml(entries[1], mid);
      cell.append(a, b);
    } else if (entries.length === 2) {
      /* Uneven pair: the chord sounding longer is the main one (ties: the
         first); the other goes into the corner inset. */
      cell.classList.add("tight");
      const durs = entries.map(([b], i) =>
        (i + 1 < entries.length ? entries[i + 1][0] : beats + 1) - b);
      const mainIdx = durs[0] >= durs[1] ? 0 : 1;
      const late = mainIdx === 0; // inset chord comes after the main one
      const main = el("div", "bx-main " + (late ? "clear-bottom" : "clear-top"));
      main.innerHTML = chordHtml(entries[mainIdx],
        late ? 1 : entries[1 - mainIdx][0] + 1);
      const inset = el("div", "bx-inset " + (late ? "inset-late" : "inset-early"));
      inset.innerHTML = chordHtml(entries[1 - mainIdx], late ? beats : 1);
      cell.append(main, inset);
    } else {
      cell.classList.add("tight");
      const wrap = el("div", "bx-halves");
      [entries.filter(([b]) => b < mid), entries.filter(([b]) => b >= mid)]
        .forEach((half, hi) => {
          const strip = el("div", "bx-half");
          half.forEach((entry, ci) => {
            const c = el("div", "bx-cell");
            c.innerHTML = chordHtml(entry, (hi === 0 ? 1 : mid) + ci);
            strip.appendChild(c);
          });
          wrap.appendChild(strip);
        });
      cell.appendChild(wrap);
    }
  }

  /* One block of box rows. The 8 columns read as two four-bar phrases: bars
     within a group of 4 share single borders (no gap), a small gap track
     separates columns 4 and 5, rows keep their own spacing. Each box carries
     its section's tint via the --bxtint custom property. */
  function renderBoxBlock(rows) {
    const block = el("div", "bx-block");
    rows.forEach((row) => {
      const rowEl = el("div", "bx-row");
      if (row.section) {
        const label = el("div", "bx-seclabel");
        label.textContent = displaySectionName(row.section);
        rowEl.appendChild(label);
      }
      row.bars.forEach((bar, i) => {
        const col = row.start + i;
        const cell = el("div", "bx");
        /* Track 5 is the mid-row gap: columns 5–8 sit in tracks 6–9. */
        cell.style.gridColumn = String(col > 4 ? col + 1 : col);
        /* Shared borders within a group of 4: only a run's first box and the
           box after the mid gap draw their own left border. */
        if (i > 0 && col !== 5) cell.classList.add("merge-left");
        if (row.tint) cell.style.setProperty("--bxhue", row.tint);
        fillBox(cell, bar, row.beats);
        rowEl.appendChild(cell);
      });
      block.appendChild(rowEl);
    });
    return block;
  }

  /* The whole book-layout view for a tune: the form badge, then one block for
     the chorus sections and a separately captioned block per auxiliary section
     (verse/intro/coda print as their own grille in the book). */
  function renderBoxGrid(tune) {
    const wrap = el("div", "boxgrid");
    const beats = beatsPerBar(tune);
    if (tune.form) {
      const formRow = el("div", "bx-formrow");
      const badge = el("div", "bx-form");
      badge.textContent = tune.form;
      formRow.appendChild(badge);
      wrap.appendChild(formRow);
    }
    let sectionNames = Object.keys(tune.sections || {});
    if (!state.showVerses) sectionNames = sectionNames.filter((n) => !VERSE_SECTION.test(n));
    /* Group into blocks: consecutive chorus sections share one lattice. */
    const blocks = [];
    sectionNames.forEach((name) => {
      const aux = AUX_SECTION.test(name);
      const last = blocks[blocks.length - 1];
      if (aux || !last || last.aux) blocks.push({ aux, sections: [name] });
      else last.sections.push(name);
    });
    blocks.forEach((blk) => {
      if (blk.aux) {
        const cap = el("div", "bx-caption");
        cap.textContent = displaySectionName(blk.sections[0]);
        wrap.appendChild(cap);
      }
      const rows = [];
      blk.sections.forEach((name) => {
        boxRowsOf(blk.aux ? null : name, tune.sections[name] || []).forEach((r) => {
          r.beats = beats;
          r.tint = sectionTint(name);
          rows.push(r);
        });
      });
      wrap.appendChild(renderBoxBlock(rows));
    });
    return wrap;
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

  /* Same, but transposed to the current target key when a transposition is
     active — so the Key/section-key chips track the transposed grid. */
  function displayKey(k) {
    if (!k || !k.tonic) return null;
    const tr = state.transpose;
    if (!tr || !tr.shift) return keyLabel(k);
    const pc = (pitchClass(k.tonic) + tr.shift) % 12;
    return keyLabel({ tonic: tr.spell[((pc % 12) + 12) % 12], mode: k.mode });
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
    const mainKey = displayKey(tune.key);
    if (mainKey) {
      const chip = harmChip("key", "Key", mainKey);
      if (state.transpose && state.transpose.shift && tune.key) {
        chip.title = "Transposed from " + keyLabel(tune.key);
      }
      keys.appendChild(chip);
    }

    /* Opening-degree badge next to the key (§8.2a) — degrees are
       key-relative, so the chip is transposition-invariant. */
    if (tune.opening && tune.opening.degree) {
      const chip = harmChip("opening", "starts on", tune.opening.degree);
      chip.title = "First chord: " + tune.opening.chord;
      keys.appendChild(chip);
    }

    /* Section keys come from the tune's own top-level `section_keys` (only
       present where a section modulates away from the main key). The copy nested
       under `key_annotation` is scorer bookkeeping and is deliberately ignored.
       Repeats of the same section that modulate identically (e.g. Chattanooga
       Choo Choo's B and B1, both to F) collapse to a single chip: dedupe on the
       displayed label + key so one "B: F major" shows, not two. */
    const sectionKeys = tune.section_keys || {};
    const seenSection = new Set();
    Object.keys(sectionKeys).forEach((name) => {
      const label = displayKey(sectionKeys[name]);
      if (!label) return;
      const shown = displaySectionName(name);
      const dedupeKey = shown + "\u0000" + label;
      if (seenSection.has(dedupeKey)) return;
      seenSection.add(dedupeKey);
      keys.appendChild(harmChip("section", shown, label));
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
       is active (set by updateStepButtons). "Add to playlist" sits in the tune
       view's top-right corner and acts on the current tune (see addPlBtn). */
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

  /* Sections that don't count toward the "chorus" bar frame the legacy captions
     use (verse/intro/… are auxiliary). Mirrors the backfill tool. */
  const AUX_SECTION = /^(verse|intro|interlude|coda|transition)/i;

  /* Chorus bars in printed order as {name, idx}, EXCLUDING auxiliary sections —
     the frame an old "applies_to" caption's bar numbers count over. Only used as
     a fallback for variants that predate the explicit `targets` anchors. */
  function chorusFlatBarsOf(tune) {
    const flat = [];
    Object.keys(tune.sections || {}).forEach((name) => {
      if (AUX_SECTION.test(name)) return;
      (tune.sections[name] || []).forEach((_bar, idx) => flat.push({ name, idx }));
    });
    return flat;
  }

  /* Resolve which main-grid bars a variant swaps in. Prefers the explicit
     `targets` anchors — one {section, bar} per occurrence, placing the variant's
     FIRST bar at that (1-indexed) grid bar with the rest following consecutively
     within the same section. Falls back to parsing the free-text "applies_to"
     over the chorus frame for un-migrated data. Returns
     { bySection: {name: {idx: variantBar}}, count } so both the grid substitution
     and the clickability check share one mapping. */
  function variantOverrides(tune, variant) {
    const bySection = {};
    let count = 0;
    const bars = (variant && variant.bars) || [];
    if (!bars.length) return { bySection, count };

    const place = (name, startIdx) => {
      const secBars = (tune.sections || {})[name];
      if (!secBars) return;
      bars.forEach((vb, i) => {
        const idx = startIdx + i;
        if (idx < 0 || idx >= secBars.length) return; // never spill past a section
        (bySection[name] || (bySection[name] = {}))[idx] = vb;
        count++;
      });
    };

    const targets = Array.isArray(variant.targets) ? variant.targets : null;
    if (targets && targets.length) {
      targets.forEach((tg) => {
        if (tg && tg.section) place(tg.section, (tg.bar || 1) - 1);
      });
    } else {
      const starts = (String(variant.applies_to || "").match(/\d+/g) || []).map(Number);
      const flat = chorusFlatBarsOf(tune);
      starts.forEach((start) => {
        const loc = flat[start - 1];
        if (loc) place(loc.name, loc.idx);
      });
    }
    return { bySection, count };
  }

  /* The grid cells a variant occupies, as a set of "section\0idx" keys — used to
     detect when two variants compete for the same bar. */
  function variantCells(tune, variant) {
    const cells = new Set();
    const bySection = variantOverrides(tune, variant).bySection;
    Object.keys(bySection).forEach((name) => {
      Object.keys(bySection[name]).forEach((idx) => cells.add(name + "\u0000" + idx));
    });
    return cells;
  }

  /* True when the two variants both override at least one common grid bar, i.e.
     they're alternatives for the same spot (e.g. My Old Flame's three bar-17
     variants) and only one may be applied at a time. */
  function variantsConflict(tune, a, b) {
    const ca = variantCells(tune, a);
    for (const c of variantCells(tune, b)) {
      if (ca.has(c)) return true;
    }
    return false;
  }

  /* Applied variants persist per tune: a { tuneId: [variantIndex,…] } map in
     localStorage, so reopening (or reloading) a tune restores the swaps the user
     had applied. */
  const VARIANTS_KEY = "grilles.variants";

  function loadVariantMap() {
    try {
      const obj = JSON.parse(localStorage.getItem(VARIANTS_KEY) || "null");
      return obj && typeof obj === "object" ? obj : {};
    } catch (e) {
      return {};
    }
  }

  /* Persist the current tune's active variant set (or drop its entry when none
     are applied). */
  function saveActiveVariants(tuneId) {
    if (!tuneId) return;
    const map = loadVariantMap();
    const arr = [...state.activeVariants].sort((a, b) => a - b);
    if (arr.length) map[tuneId] = arr;
    else delete map[tuneId];
    try {
      localStorage.setItem(VARIANTS_KEY, JSON.stringify(map));
    } catch (e) { /* ignore */ }
  }

  /* Load a tune's saved variant set into state, keeping only indices that still
     point at an applicable variant (guards against a changed corpus). */
  function restoreActiveVariants(tune, tuneId) {
    state.activeVariants = new Set();
    const saved = loadVariantMap()[tuneId];
    if (!Array.isArray(saved)) return;
    const variants = (tune && tune.variants) || [];
    saved.forEach((vi) => {
      if (variants[vi] && variantOverrides(tune, variants[vi]).count > 0) {
        state.activeVariants.add(vi);
      }
    });
  }

  /* Variants (spec §6): alternative changes for certain bars, rendered as small
     chord grids directly below the main grid — always visible, not collapsed.
     Clicking a variant swaps its bars into the main grid and back. Variants that
     touch different bars toggle independently and can be applied together (their
     overrides are merged). Variants that compete for the same bar are mutually
     exclusive: applying one drops any active variant it overlaps, so the grid
     never shows two conflicting alternatives for one bar. */
  function renderVariants(tune, beats) {
    if (!Array.isArray(tune.variants) || !tune.variants.length) return null;
    const wrap = el("div", "variants");
    const title = el("div", "variants-title");
    title.textContent = tune.variants.length > 1 ? "Variants" : "Variant";
    wrap.appendChild(title);
    tune.variants.forEach((variant, vi) => {
      const v = el("div", "variant");
      // Clickable only when we can map its anchors to real grid bars.
      const canApply = variantOverrides(tune, variant).count > 0;
      const active = state.activeVariants.has(vi);
      if (canApply) {
        v.classList.add("clickable");
        v.setAttribute("role", "button");
        v.tabIndex = 0;
        v.setAttribute("aria-pressed", active ? "true" : "false");
        v.title = active
          ? "Applied to the grid — click to restore the original bars"
          : "Click to swap these bars into the grid";
        if (active) v.classList.add("active");
        const toggle = () => {
          if (state.activeVariants.has(vi)) {
            state.activeVariants.delete(vi);
          } else {
            // Exclusive within a bar: drop any active variant that competes for
            // one of the bars this one overrides.
            state.activeVariants.forEach((other) => {
              if (other !== vi && tune.variants[other] &&
                  variantsConflict(tune, variant, tune.variants[other])) {
                state.activeVariants.delete(other);
              }
            });
            state.activeVariants.add(vi);
          }
          saveActiveVariants(state.currentId); // persist per tune
          renderTune(state.currentId); // same tune → keeps zoom/scroll/transpose
        };
        v.addEventListener("click", toggle);
        v.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
        });
      }
      if (variant.applies_to) {
        const cap = el("div", "variant-caption");
        cap.textContent = variant.applies_to;
        v.appendChild(cap);
      }
      // Same markup/classes as the main grid so it inherits its styling; the
      // fit pass (syncVariantGrids) then mirrors the main grid's fitted font
      // size and pixel width onto it so the bars line up column-for-column.
      const mini = el("div", "grid variant-grid");
      mini.appendChild(renderGrid(variant.bars || [], beats, { double: false }));
      v.appendChild(mini);
      wrap.appendChild(v);
    });
    return wrap;
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

  /* Compact key/transpose picker for the panel bar (chord tunes with a known
     key only). A native <select> stays small and gets the OS picker on mobile;
     the tune's own key is marked "(orig)" so the default is always visible.
     Changing it re-renders the tune in the chosen key (grid, variants, melody
     and the harmony key chips all follow). */
  function makeTransposeControl(t) {
    const tune = meta(t);
    const key = tune.key;
    if (!t.has_chord_json || !key || !key.tonic) return null;
    const mode = key.mode === "minor" ? "minor" : "major";
    const sourcePc = pitchClass(key.tonic);
    const tonics = mode === "minor" ? MINOR_TONICS : MAJOR_TONICS;

    const wrap = el("label", "transpose");
    wrap.title = "Transpose — original key " + noteGlyph(key.tonic) + " " + mode;
    const text = el("span", "transpose-label");
    text.textContent = "Key";
    const sel = document.createElement("select");
    sel.className = "key-select";
    const selectedPc = state.transpose ? state.transpose.targetPc : sourcePc;
    tonics.forEach((tonic, pc) => {
      const opt = document.createElement("option");
      opt.value = String(pc);
      opt.textContent = noteGlyph(tonic) + " " + mode + (pc === sourcePc ? " (orig)" : "");
      if (pc === selectedPc) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", () => {
      const targetPc = parseInt(sel.value, 10);
      if (targetPc === sourcePc) {
        state.transpose = null;
      } else {
        const shift = ((targetPc - sourcePc) % 12 + 12) % 12;
        state.transpose = { shift, spell: spellTableFor(targetPc, mode), targetPc, mode };
      }
      renderTune(state.currentId); // same tune → keeps zoom/scroll (see renderTune)
    });
    wrap.append(text, sel);
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

  /* Three-way view switch on the chords panel of a digitized tune: rendered
     grid (4 bars/row) ⇄ book layout (8 boxes/row, like the printed grille) ⇄
     original scan. The choice persists across tunes; a tune without a scan
     falls back to the grid for the scan choice without overwriting it. */
  function addChordViewSwitch(panel, t, alt) {
    panel.classList.add("has-render");
    const views = [
      ["grid", ICON_GRID, "Chord grid (4 bars per row)"],
      ["boxes", ICON_BOXES, "Book layout (8 boxes per row)"],
    ];
    if (t.chord_image) {
      [].concat(t.chord_image).forEach((src) => panel.appendChild(scanImg(src, alt)));
      views.push(["scan", ICON_IMAGE, "Original scan"]);
    }
    const tools = el("div", "panel-tools");
    const seg = el("div", "view-seg");
    const buttons = new Map();
    const apply = (view) => {
      panel.classList.toggle("show-boxes", view === "boxes");
      panel.classList.toggle("show-scan", view === "scan");
      buttons.forEach((btn, v) => {
        btn.classList.toggle("on", v === view);
        btn.setAttribute("aria-pressed", String(v === view));
      });
    };
    views.forEach(([view, icon, title]) => {
      const btn = el("button", "scan-toggle");
      btn.type = "button";
      btn.innerHTML = icon;
      btn.title = title;
      btn.setAttribute("aria-label", title);
      btn.addEventListener("click", () => {
        if (state.chordView === view) return;
        state.chordView = view;
        try {
          localStorage.setItem("grilles.chordView", view);
        } catch (e) { /* ignore */ }
        const scroll = viewEl.scrollTop;
        apply(view);
        requestAnimationFrame(fitAll); // the fit passes preserve the scroll
        /* If a scan hasn't finished loading, the panel is briefly short and
           the browser clamps the scroll — put it back once each image is in. */
        panel.querySelectorAll("img.scan").forEach((img) => {
          if (!img.complete) {
            img.addEventListener("load", () => { viewEl.scrollTop = scroll; }, { once: true });
          }
        });
      });
      buttons.set(view, btn);
      seg.appendChild(btn);
    });
    /* A persisted "scan" on a scan-less tune renders as the grid. */
    apply(buttons.has(state.chordView) ? state.chordView : "grid");
    tools.appendChild(seg);
    panel.prepend(tools);
  }

  /* Melody lead sheet from the embedded ABC (spec §5.4). Never crashes the
     page: a malformed ABC renders a warning and falls back to the scan. */
  function renderAbcSheet(panel, t, shift) {
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
        visualTranspose: shift || 0, // move with the transposed chords
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

  /* Tune-view "＋" (top-right corner): add the currently displayed tune to a
     playlist (§11.2). The popover drops from the button on desktop, becomes a
     bottom sheet on phones. */
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
    if (!tagFilterMenu.hidden && !tagFilterMenu.contains(e.target) &&
        !tagFilterBtn.contains(e.target)) {
      closeTagMenu();
    }
  });

  /* ----------------------------------- similar tunes & comparison (§8.2/§8.3) */

  function similarOf(id) {
    const d = SIMILAR[id];
    return d && (d.similar || []).length + (d.sections || []).length ? d : null;
  }

  /* Flattened 1-based bar offset of a section inside a tune's full form —
     mirrors the engine's §4.2 flattening (all sections in document order). */
  function sectionBarOffset(tune, sectionName) {
    let off = 0;
    for (const [name, bars] of Object.entries(tune.sections || {})) {
      if (name === sectionName) return off;
      off += (bars || []).length;
    }
    return 0;
  }

  function openComparison(otherId, bars) {
    state.compare = { otherId, mode: "original", bars: bars || null };
    renderTune(state.currentId);
  }

  /* Match quality bands for the score meter (score is 0–1). */
  const QUALITY_BANDS = [
    [0.85, "q-high", "very close match"],
    [0.65, "q-good", "close match"],
    [0.45, "q-fair", "clearly related"],
    [0, "q-low", "loosely related"],
  ];

  function qualityBand(score) {
    return QUALITY_BANDS.find(([min]) => score >= min);
  }

  function scoreBadge(score) {
    const pct = Math.round(score * 100);
    const [, cls, label] = qualityBand(score);
    return `<span class="sim-score ${cls}" title="${label} — ${pct}/100">` +
      `<i style="width:${pct}%"></i><b>${pct}</b></span>`;
  }

  /* The engine's suggestions for a tune, split by kind and filtered to
     bundled corpus tunes. */
  function suggestKinds(t) {
    const data = similarOf(t.id) || {};
    return {
      tunes: (data.similar || []).filter((s) => tuneById(s.id)),
      sections: (data.sections || []).filter((m) => tuneById(m.other)),
    };
  }

  /* Suggestions panel (§8.2): one group at a time — whole-tune suggestions
     or section matches ("bridge ≈ A of …") — each a list of rows with a
     colored quality meter and a short how-to hint. Clicking a row opens the
     comparison view. */
  function renderSuggestPanel(t, kind) {
    const items = suggestKinds(t)[kind];
    if (!items.length) return null;
    const panel = el("section", "suggest-panel");
    const g = el("div", "suggest-group");
    const hint = kind === "tunes"
      ? "whole tunes with a similar chord form — click one to compare the two grids side by side"
      : "single parts of this tune that match part of another tune — click to compare with the matching bars highlighted";
    g.innerHTML =
      `<div class="suggest-group-head"><span class="sg-title">` +
      `${kind === "tunes" ? "Similar tunes" : "Similar sections"}</span>` +
      `<span class="sg-hint">${hint}</span></div>`;
    panel.appendChild(g);
    /* ~5 rows visible, the rest reachable by scrolling (.suggest-list). */
    const list = el("div", "suggest-list");
    g.appendChild(list);

    if (kind === "tunes") {
      items.forEach((s) => {
        const other = tuneById(s.id);
        const row = el("button", "suggest-row");
        row.type = "button";
        row.innerHTML =
          scoreBadge(s.score) +
          `<span class="sim-title">${escapeHtml(other.title || s.id)}</span>` +
          (s.family ? `<span class="sim-family">${escapeHtml(s.family)}</span>` : "");
        row.title = "open side-by-side comparison";
        row.addEventListener("click", () => openComparison(s.id, s.bars));
        list.appendChild(row);
      });
    } else {
      items.forEach((m) => {
        const other = tuneById(m.other);
        const row = el("button", "suggest-row");
        row.type = "button";
        const local = m.other_local_key
          ? `locally in ${noteGlyph(m.other_local_key.tonic)} ${m.other_local_key.mode}` : "";
        row.innerHTML =
          scoreBadge(m.score) +
          `<span class="sim-title">${escapeHtml(
            `${displaySectionName(m.section)} ≈ ${displaySectionName(m.other_section)}` +
            ` of ${other.title || m.other}`)}</span>` +
          (local ? `<span class="sim-family">${escapeHtml(local)}</span>` : "");
        row.title = "open side-by-side comparison";
        row.addEventListener("click", () => {
          /* Section bar mappings are section-relative — shift both sides to
             flattened bar numbers so the full-form grids highlight right. */
          const qOff = sectionBarOffset(meta(t), m.section);
          const cOff = sectionBarOffset(meta(other), m.other_section);
          const bars = (m.bars || []).map(([q, c]) => [q + qOff, c + cOff]);
          openComparison(m.other, bars);
        });
        list.appendChild(row);
      });
    }
    return panel;
  }

  /* Chord renderers for the §8.3 three-way switch. */
  function cmpRenderers(qTune, cTune, mode) {
    const orig = (sym) => renderChordHTML(sym);
    const qKey = qTune.key, cKey = cTune.key;
    if (mode === "transposed" && qKey && cKey) {
      const targetPc = pitchClass(qKey.tonic);
      const shift = ((targetPc - pitchClass(cKey.tonic)) % 12 + 12) % 12;
      const spell = spellTableFor(targetPc, qKey.mode === "minor" ? "minor" : "major");
      return { q: orig, c: (sym) => renderChordHTML(transposeChordSymbol(sym, shift, spell)) };
    }
    if (mode === "roman" && qKey && cKey) {
      const deg = (tonicPc) => (sym) => {
        const d = chordDegree(sym, tonicPc);
        return `<span class="chord degree">${escapeHtml(d || "N.C.")}</span>`;
      };
      return { q: deg(pitchClass(qKey.tonic)), c: deg(pitchClass(cKey.tonic)) };
    }
    return { q: orig, c: orig };
  }

  /* One side of the comparison: the form without its verses (verses never
     enter comparisons), bars tagged with flattened full-chart numbers — the
     engine's bar numbering flattens verses and codas too, so skipped verse
     bars still advance the counter — and the aligned ones highlighted. */
  function comparisonSide(t, renderer, mapped, side, keyCaption) {
    const tune = meta(t);
    const wrap = el("div", "cmp-side");
    const head = el("div", "cmp-side-head");
    const name = el("span", "cmp-side-title");
    name.textContent = t.title || t.id;
    head.appendChild(name);
    if (keyCaption) {
      const key = el("span", "cmp-side-key");
      key.textContent = keyCaption;
      head.appendChild(key);
    }
    wrap.appendChild(head);
    const grid = el("div", "grid cmp-grid");
    const beats = beatsPerBar(tune);
    let fbar = 0;
    let first = true;
    let skippedVerse = false;
    Object.entries(tune.sections || {}).forEach(([sec, bars]) => {
      if (/^verse/i.test(sec)) {
        fbar += (bars || []).length;
        skippedVerse = true;
        return;
      }
      const secEl = renderSection(sec, bars, beats,
        first, tune.time_signature, null, renderer);
      first = false;
      secEl.querySelectorAll(".bar:not(.empty)").forEach((cell) => {
        fbar++;
        cell.dataset.fbar = String(fbar);
        cell.dataset.side = side;
        if (mapped && mapped.has(fbar)) cell.classList.add("sim-hl");
      });
      grid.appendChild(secEl);
    });
    if (skippedVerse) {
      const note = el("span", "cmp-side-note");
      note.textContent = "verse omitted";
      head.appendChild(note);
    }
    wrap.appendChild(grid);
    return wrap;
  }

  function renderComparison(t) {
    const cmp = state.compare;
    const other = tuneById(cmp.otherId);
    if (!other) { state.compare = null; return el("div"); }
    const qTune = meta(t), cTune = meta(other);

    const view = el("div", "compare");
    const bar = el("div", "cmp-bar");
    const back = el("button", "cmp-back");
    back.type = "button";
    back.textContent = "‹ Back";
    back.addEventListener("click", () => {
      state.compare = null;
      renderTune(state.currentId);
    });
    bar.appendChild(back);

    const label = el("span", "cmp-label");
    const entry = ((SIMILAR[t.id] || {}).similar || []).find((s) => s.id === cmp.otherId);
    label.innerHTML = `vs ${escapeHtml(other.title || cmp.otherId)}` +
      (entry ? ` ${scoreBadge(entry.score)}` : "");
    bar.appendChild(label);

    /* Three-way display switch (§8.3, locked decision §1). */
    const canKey = Boolean(qTune.key && cTune.key);
    const modes = [
      ["original", "Original keys", true],
      ["transposed", qTune.key ? `In ${noteGlyph(qTune.key.tonic)} ${qTune.key.mode}` : "Transposed", canKey],
      ["roman", "Degrees", canKey],
    ];
    const switcher = el("div", "cmp-modes");
    modes.forEach(([mode, text, enabled]) => {
      const btn = el("button", "cmp-mode" + (cmp.mode === mode ? " active" : ""));
      btn.type = "button";
      btn.textContent = text;
      btn.dataset.mode = mode;
      btn.disabled = !enabled;
      btn.addEventListener("click", () => {
        state.compare.mode = mode;
        renderTune(state.currentId);
      });
      switcher.appendChild(btn);
    });
    bar.appendChild(switcher);
    view.appendChild(bar);

    /* Bar maps: flattened query bar -> candidate bars, and the reverse. */
    const mapQ = new Map(), mapC = new Map();
    (cmp.bars || []).forEach(([q, c]) => {
      if (!mapQ.has(q)) mapQ.set(q, []);
      mapQ.get(q).push(c);
      if (!mapC.has(c)) mapC.set(c, []);
      mapC.get(c).push(q);
    });

    const r = cmpRenderers(qTune, cTune, cmp.mode);
    const keyCap = (tune, transposedTo) => {
      if (!tune.key) return "";
      if (cmp.mode === "roman") return "degrees";
      if (transposedTo) return `in ${noteGlyph(transposedTo.tonic)} ${transposedTo.mode} (from ${noteGlyph(tune.key.tonic)} ${tune.key.mode})`;
      return `${noteGlyph(tune.key.tonic)} ${tune.key.mode}`;
    };
    const panels = el("div", "cmp-panels");
    panels.appendChild(comparisonSide(t, r.q, new Set(mapQ.keys()), "q", keyCap(qTune)));
    panels.appendChild(comparisonSide(other, r.c, new Set(mapC.keys()), "c",
      keyCap(cTune, cmp.mode === "transposed" && canKey ? qTune.key : null)));
    view.appendChild(panels);

    /* Hovering an aligned bar lights up its counterpart(s) on the other side. */
    panels.querySelectorAll(".bar.sim-hl").forEach((cell) => {
      const map = cell.dataset.side === "q" ? mapQ : mapC;
      const counterparts = map.get(parseInt(cell.dataset.fbar, 10)) || [];
      const otherSide = cell.dataset.side === "q" ? "c" : "q";
      cell.addEventListener("mouseenter", () => {
        panels.querySelectorAll(`.bar[data-side="${otherSide}"]`).forEach((o) => {
          o.classList.toggle("sim-hl-active",
            counterparts.includes(parseInt(o.dataset.fbar, 10)));
        });
        cell.classList.add("sim-hl-active");
      });
      cell.addEventListener("mouseleave", () => {
        panels.querySelectorAll(".sim-hl-active").forEach((o) =>
          o.classList.remove("sim-hl-active"));
      });
    });
    return view;
  }

  /* ----------------------------------------------------------- main render */

  function renderTune(id) {
    const t = tuneById(id);
    if (!t) return;
    /* A genuine tune change resets the per-tune transposition, zoom and scroll;
       re-rendering the same tune (e.g. after picking a new key) preserves them. */
    const isNewTune = id !== state.currentId;
    state.currentId = id;
    if (isNewTune) {
      state.transpose = null; // every tune opens in its own printed key
      state.gridZoom = 1; // grid zoom is per-tune: every tune opens at the fitted size
      state.compare = null; // comparison view is per-tune
      state.showSuggest = null; // suggestions panel is per-tune
      restoreActiveVariants(meta(t), id); // restore the user's saved variant swaps
    }
    document.title = `${t.title || id} — Grilles`;

    paneEl.innerHTML = "";
    paneEl.appendChild(renderHead(t));

    /* Comparison view (§8.3) replaces the normal panels until Back. */
    if (state.compare) {
      paneEl.appendChild(renderComparison(t));
      if (isNewTune) viewEl.scrollTop = 0;
      if (narrowMq.matches) setListOpen(false);
      updateListHighlight();
      updateStepButtons();
      closePopover();
      return;
    }

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
    /* Only offered when the tune has a verse — the grid rebuilds on toggle, and
       re-rendering the same tune keeps zoom/scroll/transpose/variants. */
    if (t.has_chord_json && hasVerseSection(t)) {
      bar.appendChild(makeSwitch("Verses", state.showVerses, (on) => {
        state.showVerses = on;
        saveSwitch("grilles.showVerses", on);
        renderTune(state.currentId);
      }));
    }
    const transpose = makeTransposeControl(t);
    if (transpose) bar.appendChild(transpose);
    /* Suggestion buttons (§8.2) — one per kind the engine produced,
       colored by the best match's quality band. */
    const kinds = suggestKinds(t);
    [["tunes", "Similar tunes"], ["sections", "Similar sections"]]
      .forEach(([kind, label]) => {
        const items = kinds[kind];
        if (!items.length) return;
        const best = Math.max(...items.map((x) => x.score));
        const [, cls, bandLabel] = qualityBand(best);
        const on = state.showSuggest === kind;
        const btn = el("button", `suggest-btn ${cls}` + (on ? " on" : ""));
        btn.type = "button";
        btn.textContent = label;
        btn.title = `best match: ${bandLabel} (${Math.round(best * 100)}/100)`;
        btn.setAttribute("aria-pressed", String(on));
        btn.addEventListener("click", () => {
          state.showSuggest = on ? null : kind;
          renderTune(state.currentId); // same tune → keeps zoom/scroll/transpose
        });
        bar.appendChild(btn);
      });
    if (bar.childElementCount) paneEl.appendChild(bar);

    if (state.showSuggest) {
      const suggest = renderSuggestPanel(t, state.showSuggest);
      if (suggest) paneEl.appendChild(suggest);
    }

    const panels = el("div", "panels");

    if (hasChordAsset(t)) {
      const panel = el("section", "panel chords");
      const tune = meta(t);
      if (t.has_chord_json) {
        const grid = el("div", "grid");
        const beats = beatsPerBar(tune);
        // Merge the per-section bar substitutions of every active variant (empty
        // when none). Later variants win on any bar two of them both touch.
        const overrides = {};
        if (Array.isArray(tune.variants)) {
          state.activeVariants.forEach((vi) => {
            const variant = tune.variants[vi];
            if (!variant) return;
            const bySection = variantOverrides(tune, variant).bySection;
            Object.keys(bySection).forEach((name) => {
              Object.assign(overrides[name] || (overrides[name] = {}), bySection[name]);
            });
          });
        }
        let sectionNames = Object.keys(tune.sections || {});
        if (!state.showVerses) sectionNames = sectionNames.filter((n) => !VERSE_SECTION.test(n));
        sectionNames.forEach((name, i) => {
          grid.appendChild(renderSection(name, tune.sections[name], beats,
            i === 0, tune.time_signature, overrides[name]));
        });
        panel.appendChild(grid);
        panel.appendChild(renderBoxGrid(tune)); // book layout (hidden unless chosen)
        const variants = renderVariants(tune, beats);
        if (variants) panel.appendChild(variants);
        const extras = renderExtras(tune, beats);
        if (extras) panel.appendChild(extras);
        addChordViewSwitch(panel, t, `${t.title || id} — original chord scan`);
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
    /* abcjs measures the container, so render after the panel is in the DOM.
       The melody moves with the chords: same semitone shift via visualTranspose. */
    if (melodyPanel) renderAbcSheet(melodyPanel, t, state.transpose ? state.transpose.shift : 0);
    applyPanels(t);

    if (isNewTune) viewEl.scrollTop = 0;
    if (narrowMq.matches) setListOpen(false);
    updateListHighlight();
    updateStepButtons();
    closePopover(); // an open add-popover belongs to the previous tune
  }

  /*
   * The grid's base font size is width-driven — the CSS clamp on .grid (up to
   * the 15px ceiling), narrowed by fitGridWidth only when a bar's chords would
   * collide. It is deliberately NOT shrunk to fit the viewport height: a long
   * tune (many sections) stays at a readable size and the page scrolls, rather
   * than collapsing the chords to an unreadable size to force one screen page.
   * This pass only applies the user's zoom factor on top of that width-driven
   * base size.
   */
  const MIN_GRID_FONT = 8; // px — floor for the width-crowding shrink in fitGridWidth

  function fitGrid() {
    const grid = paneEl.querySelector(".panel.chords:not([hidden]):not(.show-scan):not(.show-boxes) .grid:not(.variant-grid)");
    if (!grid) return;
    grid.style.fontSize = ""; // back to the CSS width-based size
    if (state.gridZoom !== 1) {
      const base = parseFloat(getComputedStyle(grid).fontSize);
      grid.style.fontSize = Math.max(6, base * state.gridZoom).toFixed(2) + "px";
    }
  }

  /* Zoom is transient per-tune (reset in renderTune), so it isn't persisted. */
  function setGridZoom(zoom) {
    state.gridZoom = Math.min(2.5, Math.max(0.5, zoom));
    fitAll();
  }

  zoomInBtn.addEventListener("click", () => setGridZoom(state.gridZoom * 1.15));
  zoomOutBtn.addEventListener("click", () => setGridZoom(state.gridZoom / 1.15));

  /* Chords never shrink relative to their slots by their own doing — every chord
   * renders at the grid's single font size. When the busiest bar's chords would
   * collide (spec §6.4) there are two levers, applied in order:
   *   1. Widen the grid, so each slot gets more room (works on wide screens).
   *   2. When there's no width left to give — a narrow phone or the chords panel
   *      sharing the row with the melody — pin the grid to the available width
   *      and shrink the font instead, so the chords fit the now-fixed-px slots.
   * The overflow ratio is font-independent (chord and slot both scale with the
   * font), so both levers are computed from one measurement pass. */
  const MAX_GRID_WIDTH_EM = 56; // aesthetic cap: past this, shrink the font instead
  const MIN_BEAT_GAP_EM = 0.1; // minimum gap kept between a chord and the next beat's chord
  const PER_CHORD_OVERFLOW_EM = 0.20; // how far a chord may poke past its beat column before it counts as crowding
  const MAX_MOBILE_FONT = 20; // px — ceiling for the grow-to-fill pass on phones/portrait

  function fitGridWidth() {
    const grid = paneEl.querySelector(".panel.chords:not([hidden]):not(.show-scan):not(.show-boxes) .grid:not(.variant-grid)");
    if (!grid) return;
    grid.style.maxWidth = "";

    // The width the grid may occupy if unconstrained = its container's content
    // box (grid is a block that fills its parent up to max-width).
    grid.style.maxWidth = "none";
    const availPx = grid.getBoundingClientRect().width;
    grid.style.maxWidth = ""; // back to the CSS default (fills the panel), the starting width
    const curPx = grid.getBoundingClientRect().width;
    if (!availPx || !curPx) return;

    // Crowding is measured per chord-span, not per bar: on the fixed beat grid a
    // chord occupies only its own beat column(s) and can't borrow a neighbour's
    // room, so it collides when its width exceeds its span (nextBeat − beat) worth
    // of columns (less a minimum gap). ratio = the worst such (chord width ÷ span
    // room) over every bar; it's font-proportional (chord width and gap both scale
    // with the font), so this one pass drives every lever below. >1 collides, <1
    // has slack the grow-to-fill pass can spend on a larger font.
    //
    // A small per-chord overflow allowance lets a wide chord poke slightly past
    // its own column into the whitespace before the next chord's ink, so a dense
    // bar can stay a bit larger without the chords actually touching — it trades a
    // touch of that gap for font size, without disturbing the beat alignment
    // (chords stay anchored to their beat lines).
    const fontPx = parseFloat(getComputedStyle(grid).fontSize);
    const gapPx = MIN_BEAT_GAP_EM * fontPx;
    const allowPx = PER_CHORD_OVERFLOW_EM * fontPx;
    let ratio = 0;
    grid.querySelectorAll(".bar").forEach((bar) => {
      const slots = bar.querySelectorAll(".slot");
      if (!slots.length) return;
      const cs = getComputedStyle(bar);
      const inner = bar.clientWidth -
        (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
      if (inner <= 0) return;
      const beats = parseInt(bar.dataset.beats, 10) || 4;
      const colW = inner / beats;
      slots.forEach((slot) => {
        const span = parseInt(slot.dataset.span, 10) || 1;
        const room = span * colW - gapPx + allowPx; // span, less a min gap, plus overflow
        if (room <= 0) return;
        ratio = Math.max(ratio, slot.getBoundingClientRect().width / room);
      });
    });
    if (ratio <= 0) return; // no bars carry chords

    // Phones / portrait: the grid always fills the full panel width, and the font
    // grows (or shrinks) so the busiest bar just fits — the chords get as large as
    // the crowding allows instead of being pinned to a small width-based size. The
    // fitted size is font-independent (both `fontPx` and `ratio` scale together),
    // so the user's zoom is re-applied as a plain multiplier on top of it.
    if (narrowMq.matches) {
      grid.style.maxWidth = availPx.toFixed(1) + "px";
      // 0.97 leaves a hair of slack so sub-pixel/letter-spacing rounding can't push
      // the busiest bar's last chord into the barline.
      const fitPx = fontPx * (availPx / curPx) / ratio * 0.97;
      const base = Math.min(MAX_MOBILE_FONT, fitPx);
      const next = Math.max(MIN_GRID_FONT, base * state.gridZoom);
      grid.style.fontSize = next.toFixed(2) + "px";
      return;
    }

    // Desktop: keep the grid centred at its width-based size, only reacting when a
    // busy bar would collide — widen first, then shrink the font if there's no
    // room left to give.
    if (ratio <= 1.001) return;
    const neededPx = curPx * ratio; // grid width at which chords would just fit
    const widenCap = Math.min(availPx, MAX_GRID_WIDTH_EM * fontPx);
    if (neededPx <= widenCap) {
      grid.style.maxWidth = neededPx.toFixed(1) + "px"; // lever 1: widen only
      return;
    }
    // Lever 2: take all the room there is, then shrink the font by what's left.
    grid.style.maxWidth = widenCap.toFixed(1) + "px";
    const residual = neededPx / widenCap;
    const next = Math.max(MIN_GRID_FONT, fontPx / residual);
    grid.style.fontSize = next.toFixed(2) + "px";
  }

  /* Variant grids mirror the main grid exactly — the same fitted font size and
     the same centred pixel width — so every variant barline lines up with the
     main grid's columns above it (spec §6). Runs after the main grid is fitted. */
  function syncVariantGrids() {
    const panel = paneEl.querySelector(".panel.chords:not([hidden]):not(.show-scan):not(.show-boxes)");
    if (!panel) return;
    const main = panel.querySelector(".grid:not(.variant-grid)");
    if (!main) return;
    const fontPx = getComputedStyle(main).fontSize;
    const widthPx = main.getBoundingClientRect().width.toFixed(1) + "px";
    panel.querySelectorAll(".variant-grid").forEach((g) => {
      g.style.fontSize = fontPx;
      g.style.maxWidth = widthPx;
    });
    // Keep the "Variant(s)" heading and per-variant captions the same width and
    // centring as the grid, so they sit flush above the aligned bars.
    panel.querySelectorAll(".variants-title, .variant-caption").forEach((n) => {
      n.style.maxWidth = widthPx;
    });
  }

  /* Book-layout view: apply the user's zoom to the lattice font, then squeeze
     each chord into its box (or its part of the box — a diagonal half, a
     side-by-side slot): any chord wider than its region gets a per-chord
     font-size cut so it fits, instead of overflowing across the lattice line.
     Regions are measured fresh each pass (inline sizes reset first), so
     resize / zoom / transpose all re-fit correctly. */
  const BOX_PAD_EM = 0.35; // breathing room kept inside a box, per side
  const MIN_BOX_CHORD_PX = 6; // px floor for the per-chord squeeze

  function fitBoxes() {
    const bg = paneEl.querySelector(
      ".panel.chords:not([hidden]).show-boxes:not(.show-scan) .boxgrid");
    if (!bg) return;
    bg.style.fontSize = "";
    if (state.gridZoom !== 1) {
      const base = parseFloat(getComputedStyle(bg).fontSize);
      bg.style.fontSize = Math.max(6, base * state.gridZoom).toFixed(2) + "px";
    }
    const fontPx = parseFloat(getComputedStyle(bg).fontSize);
    const padPx = BOX_PAD_EM * fontPx;
    bg.querySelectorAll(".bx").forEach((box) => {
      const inner = box.clientWidth - 2 * padPx;
      if (inner <= 0) return;
      /* region width per chord container inside this box */
      const parts = [];
      box.querySelectorAll(".bx-solo").forEach((p) => parts.push([p, inner]));
      /* Triangle chords centre near their centroid, backed off the diagonal;
         growing symmetrically from 27%, they may take about 55% of the width
         before touching the line. */
      box.querySelectorAll(".bx-a, .bx-b").forEach((p) => parts.push([p, inner * 0.55]));
      /* Uneven pair: the main chord centres in the half-strip opposite the
         inset, with nearly the whole box width to itself. */
      box.querySelectorAll(".bx-main").forEach((p) => parts.push([p, inner * 0.9]));
      box.querySelectorAll(".bx-inset").forEach((p) => parts.push([p, inner * 0.46]));
      /* Halved box: each cell owns its flex share of the strip. */
      box.querySelectorAll(".bx-cell").forEach((p) =>
        parts.push([p, p.clientWidth - 0.3 * fontPx]));
      parts.forEach(([part, avail]) => {
        if (!part.querySelector(".chord")) return;
        part.style.fontSize = "";
        /* Content width, not the container's: .bx-solo spans its whole box. */
        const w = Array.from(part.children)
          .reduce((s, c) => s + c.getBoundingClientRect().width, 0);
        if (w <= avail) return;
        const cur = parseFloat(getComputedStyle(part).fontSize);
        part.style.fontSize =
          Math.max(MIN_BOX_CHORD_PX, cur * (avail / w)).toFixed(2) + "px";
      });
    });
  }

  function fitAll() {
    fitGrid();
    fitGridWidth();
    syncVariantGrids();
    fitBoxes();
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
    /* Defaults: Chords on, Melody on when the tune has one (spec §5.4); a
       persisted choice ("0"/"1") wins over the default. */
    const c = localStorage.getItem("grilles.showChords");
    const m = localStorage.getItem("grilles.showMelody");
    const v = localStorage.getItem("grilles.showVerses");
    state.showChords = c === null ? true : c === "1";
    state.showMelody = m === null ? true : m === "1";
    state.showVerses = v === null ? true : v === "1";
    const cv = localStorage.getItem("grilles.chordView");
    if (cv === "grid" || cv === "boxes" || cv === "scan") state.chordView = cv;
    state.boxTint = localStorage.getItem("grilles.boxTint") !== "0";
    applyTintToggle();
    state.chordsOnly = localStorage.getItem("grilles.chordsOnly") === "1";
    if (localStorage.getItem("grilles.list") === "0") setListCollapsed(true);
  } catch (e) { /* ignore */ }
  chordFilterBtn.classList.toggle("on", state.chordsOnly);
  chordFilterBtn.setAttribute("aria-pressed", String(state.chordsOnly));
  /* Restore the active playlist (§11.4); a stale id starts deactivated. */
  const storedPl = PL.getActiveId();
  if (storedPl && PL.byId(storedPl)) state.activePl = storedPl;
  updateTopbar();
  initStartsOnFilter();
  initKeyFilter();
  initFormFilter();
  initTagFilter();
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
