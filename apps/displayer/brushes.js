/* Swing-brush practice player. A topbar transport (play/stop, ♩=BPM popover
   with slider, tap tempo, pattern and volume) drives a Web Audio look-ahead
   scheduler over the offline-rendered brush samples (data/brush_samples.js,
   built by render_brush_samples.py), while a playhead steps bar-by-bar and
   beat-by-beat through whichever chord layout (grid or book) is visible.

   Groove: the classic ride "spang-a-lang" tap pattern with a triplet-swing
   skip note, plus — in "brush kit" mode — a continuous snare-sweep loop whose
   gain swells into 2 & 4 and a soft hi-hat chick on 2 & 4. 3/4 tunes get the
   jazz-waltz variant automatically; other meters fall back to plain quarters.
   Playback starts with a one-bar count-in and loops the whole form. */
"use strict";

(function () {
  const playBtn = document.getElementById("brushPlay");
  const bpmBtn = document.getElementById("brushBtn");
  const menu = document.getElementById("brushMenu");
  const paneEl = document.getElementById("tunePane");
  if (!playBtn || !bpmBtn || !menu) return;

  const BPM_MIN = 40;
  const BPM_MAX = 300;
  const LOOKAHEAD = 0.12; // s of audio scheduled ahead of the clock
  const TICK_MS = 25; // scheduler timer period
  const SWISH_LEVEL = 0.55; // sweep loop level relative to the taps

  /* Keep in sync with app.js/style.css narrow seam (popover becomes a sheet). */
  const narrowMq = window.matchMedia(
    "(max-width: 700px), (max-height: 500px), (max-width: 899px) and (orientation: portrait)");

  /* ---------------------------------------------------- persisted settings */

  let bpm = 140;
  let pattern = "full"; // "full" (kit) | "taps" (ride pattern only)
  let source = "synth"; // "synth" (rendered kit) | "loop" (real recording)
  let volume = 0.8;
  try {
    const b = parseInt(localStorage.getItem("grilles.brushBpm"), 10);
    if (Number.isFinite(b)) bpm = Math.min(BPM_MAX, Math.max(BPM_MIN, b));
    if (localStorage.getItem("grilles.brushPattern") === "taps") pattern = "taps";
    if (localStorage.getItem("grilles.brushSource") === "loop") source = "loop";
    const v = parseFloat(localStorage.getItem("grilles.brushVol"));
    if (Number.isFinite(v) && v >= 0 && v <= 1) volume = v;
  } catch (e) { /* ignore */ }

  function persist(key, val) {
    try { localStorage.setItem(key, String(val)); } catch (e) { /* ignore */ }
  }

  /* -------------------------------------------------------------- samples */

  let ctx = null;
  let master = null;
  let buffers = null; // {taps:[], accents:[], hats:[], swish}
  let loadPromise = null;

  function base64Buffer(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  /* The audio bundles are hundreds of kB, so they are injected lazily on
     first use rather than loaded with the page (script tag: fetch() is
     unavailable on file://, where the app must keep working). */
  function loadScriptOnce(src, globalName, hint) {
    return new Promise((resolve, reject) => {
      if (window[globalName]) return resolve(window[globalName]);
      const s = document.createElement("script");
      s.src = src;
      s.onload = () => (window[globalName] ? resolve(window[globalName])
        : reject(new Error(src + " loaded but defined nothing")));
      s.onerror = () => reject(new Error(src + " missing — run " + hint));
      document.head.appendChild(s);
    });
  }

  function loadSampleScript() {
    return loadScriptOnce("data/brush_samples.js", "BRUSH_SAMPLES",
      "apps/displayer/render_brush_samples.py");
  }

  function decode(b64) {
    return ctx.decodeAudioData(base64Buffer(b64));
  }

  function ensureAudio() {
    if (!ctx) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -18;
      comp.knee.value = 12;
      comp.ratio.value = 4;
      master = ctx.createGain();
      master.gain.value = volume;
      master.connect(comp);
      comp.connect(ctx.destination);
    }
    if (!loadPromise) {
      loadPromise = loadSampleScript().then((S) => Promise.all([
        Promise.all(S.taps.map(decode)),
        Promise.all(S.accents.map(decode)),
        Promise.all(S.hats.map(decode)),
        decode(S.swish),
      ]).then(([taps, accents, hats, swish]) => {
        buffers = { taps, accents, hats, swish };
      }));
    }
    return loadPromise;
  }

  /* ----------------------------------------------------------- real loops
   *
   * data/brush_loops.js (embed_brush_loops.py) ships CC0 recordings with
   * their native BPM/meter. One loop per meter, rate-stretched to the chosen
   * tempo and restarted at every form top so chart alignment never drifts.
   * Meters without a loop (e.g. 3/4) silently fall back to the synth kit.
   */
  let loopMeta = null; // metadata list, available before any AudioContext
  let loopBuffers = null; // same order as loopMeta
  let loopLoadPromise = null;
  let loopSrc = null;
  let loopGain = null;

  function ensureLoopMeta() {
    return loadScriptOnce("data/brush_loops.js", "BRUSH_LOOPS",
      "apps/displayer/embed_brush_loops.py").then((L) => { loopMeta = L; return L; });
  }

  function ensureLoops() {
    if (!loopLoadPromise) {
      loopLoadPromise = ensureLoopMeta()
        .then((L) => Promise.all(L.map((l) => decode(l.ogg))))
        .then((bufs) => { loopBuffers = bufs; });
    }
    return loopLoadPromise;
  }

  function loopForBeats(n) {
    if (!loopMeta || !loopBuffers) return null;
    const i = loopMeta.findIndex((l) => l.beatsPerBar === n);
    return i >= 0 ? { meta: loopMeta[i], buffer: loopBuffers[i] } : null;
  }

  /* Start (or seamlessly restart, 15 ms crossfade) the loop at time t.
     `beatsIn` is how far into the form we join — nonzero when the source is
     switched mid-take — mapped to the same spot in the recording. */
  function startLoopAt(loop, t, beatsIn) {
    const rate = bpm / loop.meta.bpm;
    const old = loopSrc;
    const oldGain = loopGain;
    loopGain = ctx.createGain();
    loopGain.gain.setValueAtTime(0, t);
    loopGain.gain.linearRampToValueAtTime(1, t + 0.015);
    loopGain.connect(master);
    loopSrc = ctx.createBufferSource();
    loopSrc.buffer = loop.buffer;
    loopSrc.loop = true;
    loopSrc.playbackRate.value = rate;
    loopSrc.connect(loopGain);
    const offset = (beatsIn * 60 / loop.meta.bpm) % loop.buffer.duration;
    loopSrc.start(Math.max(t, ctx.currentTime), offset);
    if (old) {
      oldGain.gain.setValueAtTime(1, t);
      oldGain.gain.linearRampToValueAtTime(0, t + 0.015);
      old.stop(t + 0.05);
    }
  }

  function stopLoop(t) {
    if (!loopSrc) return;
    loopGain.gain.setTargetAtTime(0, t, 0.02);
    loopSrc.stop(t + 0.2);
    loopSrc = null;
    loopGain = null;
  }

  function scheduleLoopBeat(loop, t, beat) {
    if (!loopSrc || (beat === 1 && barIdx === 0)) {
      startLoopAt(loop, t, loopSrc ? 0 : barIdx * beats + (beat - 1));
    } else {
      loopSrc.playbackRate.setValueAtTime(bpm / loop.meta.bpm, t);
    }
  }

  /* ------------------------------------------------- tune + playhead cells */

  function currentTune() {
    const id = decodeURIComponent(location.hash.slice(1));
    const t = (window.TUNES || []).find((x) => x.id === id);
    return (t && t.tune) || null;
  }

  function beatsPerBar() {
    const tune = currentTune();
    const num = parseInt(String((tune && tune.time_signature) || "4/4").split("/")[0], 10);
    return Number.isFinite(num) && num > 0 && num <= 12 ? num : 4;
  }

  function isVisible(elm) {
    return !!elm && elm.offsetWidth > 0 && elm.offsetHeight > 0;
  }

  /* The bar cells of whichever chord layout is on screen, in form order.
     Grid view: .bar cells (empty row-fillers excluded). Book view: the
     lattice's boxes only — variant boxes live under .bx-variant and are
     skipped by the direct-child block selector. Empty when the chords panel
     is hidden, showing the scan, or the tune isn't digitized. */
  let cells = [];
  function queryCells() {
    if (!paneEl) return [];
    const grid = paneEl.querySelector(".panel.chords .grid");
    if (isVisible(grid)) return [...grid.querySelectorAll(".bar:not(.empty)")];
    const boxes = paneEl.querySelector(".panel.chords .boxgrid");
    if (isVisible(boxes)) return [...boxes.querySelectorAll(":scope > .bx-block .bx")];
    return [];
  }

  /* Bars in the tune data — loop length fallback when no cells are rendered
     (e.g. scan view). 0 = free-run (loop nothing, just keep the groove). */
  function metaBarCount() {
    const tune = currentTune();
    if (!tune || !tune.sections) return 0;
    return Object.values(tune.sections).reduce((n, bars) => n + bars.length, 0);
  }

  function refreshCells() {
    cells = queryCells();
    const total = cells.length || metaBarCount();
    if (total && barIdx >= total) barIdx = 0;
  }

  /* ------------------------------------------------------------ transport */

  let playing = false;
  let timer = null;
  let rafId = 0;
  let nextTime = 0; // AudioContext time of the next unscheduled beat
  let countLeft = 0; // count-in beats still to schedule
  let barIdx = 0;
  let beatInBar = 1;
  let beats = 4;
  let visualQueue = []; // {time, barIdx, beat, beats, count}
  let swishSrc = null;
  let swishGain = null;

  /* Swing narrows as the tempo climbs: full triplet feel (2:1) up to medium
     tempos, flattening toward straighter 8ths on burners. */
  function swingRatio() {
    const t = Math.max(0, Math.min(1, (bpm - 150) / 110));
    return 0.66 - 0.08 * t;
  }

  function pick(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
  }

  /* One sample at time t: velocity plus a little human wobble in level,
     timbre (playback rate) and timing. */
  function hit(buf, t, vel, rate) {
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.playbackRate.value = (rate || 1) * (0.96 + Math.random() * 0.08);
    const g = ctx.createGain();
    g.gain.value = vel * (0.92 + Math.random() * 0.16);
    src.connect(g);
    g.connect(master);
    src.start(Math.max(t + (Math.random() * 0.004 - 0.002), ctx.currentTime));
  }

  /* Count-in: dry stick-like clicks (taps sped up), accent on 1. */
  function scheduleCount(t, beat) {
    hit(pick(buffers.taps), t, beat === 1 ? 0.9 : 0.5, 1.45);
    if (swishGain) swishGain.gain.setValueAtTime(0.02, t);
  }

  /* Sweep swell target at each beat; linearRamp from the previous point makes
     the circular sweep rise with the skip pair into each accent (1 & 3 in
     4/4, the bar start in 3/4), reinforcing the long-short-short anchoring. */
  function swishTarget(beat) {
    if (beats === 4) return beat === 1 || beat === 3 ? 1 : 0.3;
    if (beats === 3) return [0, 1, 0.4, 0.7][beat];
    return beat === 1 ? 0.9 : 0.4;
  }

  function scheduleBeat(t, beat, dur) {
    const full = pattern === "full";
    if (beats === 4) {
      /* Long-short-short cells anchored on the accents (owner, 2026-07): the
         accent (with hat chick and sweep peak) opens each 2-beat cell on 1 &
         3, a full beat follows (long), then the swing skip after 2 & 4 is the
         short-short pair leading back into the next accent. Anchored on the
         accent the ear hears LONG-short-short, not short-short-long. */
      const strong = beat === 1 || beat === 3;
      hit(strong ? pick(buffers.accents) : pick(buffers.taps), t, strong ? 0.95 : 0.68);
      if (!strong) hit(pick(buffers.taps), t + dur * swingRatio(), 0.42);
      if (full && strong) hit(pick(buffers.hats), t, 0.7);
    } else if (beats === 3) {
      /* Jazz waltz, same anchoring per bar: accent opens the bar (long to 2),
         skip after 2 is the short-short pair leading back into 1. */
      hit(beat === 1 ? pick(buffers.accents) : pick(buffers.taps), t, beat === 1 ? 0.9 : 0.62);
      if (beat === 2) {
        hit(pick(buffers.taps), t + dur * swingRatio(), 0.4);
        if (full) hit(pick(buffers.hats), t, 0.6);
      }
    } else {
      hit(beat === 1 ? pick(buffers.accents) : pick(buffers.taps), t, beat === 1 ? 0.9 : 0.6);
    }
    if (full && swishGain) swishGain.gain.linearRampToValueAtTime(swishTarget(beat), t);
  }

  function scheduler() {
    while (nextTime < ctx.currentTime + LOOKAHEAD) {
      const dur = 60 / bpm;
      if (countLeft > 0) {
        const beat = beats - countLeft + 1;
        scheduleCount(nextTime, beat);
        visualQueue.push({ time: nextTime, count: true, beat, beats });
        countLeft--;
        if (countLeft === 0) {
          barIdx = 0;
          beatInBar = 1;
        }
      } else {
        const loop = source === "loop" ? loopForBeats(beats) : null;
        if (loop) scheduleLoopBeat(loop, nextTime, beatInBar);
        else {
          if (loopSrc) stopLoop(nextTime); // source switched (or meter lost its loop)
          scheduleBeat(nextTime, beatInBar, dur);
        }
        visualQueue.push({ time: nextTime, barIdx, beat: beatInBar, beats });
        beatInBar++;
        if (beatInBar > beats) {
          beatInBar = 1;
          refreshCells(); // re-renders/view switches are picked up per bar
          beats = beatsPerBar();
          const total = cells.length || metaBarCount();
          barIdx = total ? (barIdx + 1) % total : 0;
        }
      }
      nextTime += dur;
    }
  }

  /* ------------------------------------------------------------- playhead */

  let curCell = null;
  const cursorEl = document.createElement("div");
  cursorEl.className = "brush-cursor";

  function clearPlayhead() {
    if (curCell) curCell.classList.remove("brush-now");
    curCell = null;
    cursorEl.remove();
  }

  function applyVisual(ev) {
    setDots(ev);
    if (ev.count) {
      clearPlayhead();
      return;
    }
    if (!cells.length || !cells[ev.barIdx] || !cells[ev.barIdx].isConnected) refreshCells();
    const cell = cells[ev.barIdx];
    if (!cell) {
      clearPlayhead();
      return;
    }
    if (cell !== curCell) {
      if (curCell) curCell.classList.remove("brush-now");
      cell.classList.add("brush-now");
      curCell = cell;
      cell.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    cursorEl.style.left = ((ev.beat - 1) / ev.beats) * 100 + "%";
    cursorEl.style.width = (1 / ev.beats) * 100 + "%";
    if (cursorEl.parentNode !== cell) cell.appendChild(cursorEl);
  }

  function visualLoop() {
    if (!playing) return;
    const now = ctx.currentTime + 0.02;
    while (visualQueue.length && visualQueue[0].time <= now) {
      applyVisual(visualQueue.shift());
    }
    rafId = requestAnimationFrame(visualLoop);
  }

  /* ----------------------------------------------------------- start/stop */

  function start() {
    playBtn.disabled = true;
    const wanted = [ensureAudio()];
    /* A failing loop bundle falls back to the synth kit rather than blocking
       playback. */
    if (source === "loop") {
      wanted.push(ensureLoops().catch((err) => {
        console.error(err);
        applySource("synth");
      }));
    }
    Promise.all(wanted).then(() => {
      playBtn.disabled = false;
      if (playing) return;
      if (ctx.state === "suspended") ctx.resume();
      playing = true;
      refreshCells();
      beats = beatsPerBar();
      barIdx = 0;
      beatInBar = 1;
      countLeft = beats; // one-bar count-in
      visualQueue = [];
      /* The sweep texture loops for the whole take; the per-beat gain
         automation shapes it (and holds it at 0 in taps-only mode). */
      swishGain = ctx.createGain();
      swishGain.gain.value = 0;
      const bus = ctx.createGain();
      bus.gain.value = SWISH_LEVEL;
      swishSrc = ctx.createBufferSource();
      swishSrc.buffer = buffers.swish;
      swishSrc.loop = true;
      swishSrc.connect(swishGain);
      swishGain.connect(bus);
      bus.connect(master);
      swishSrc.start();
      nextTime = ctx.currentTime + 0.1;
      scheduler();
      timer = setInterval(scheduler, TICK_MS);
      rafId = requestAnimationFrame(visualLoop);
      playBtn.textContent = "■";
      playBtn.classList.add("on");
      playBtn.setAttribute("aria-pressed", "true");
    }).catch((err) => {
      playBtn.disabled = false;
      console.error(err);
      bpmBtn.title = String(err.message || err);
    });
  }

  function stop() {
    playing = false;
    clearInterval(timer);
    timer = null;
    cancelAnimationFrame(rafId);
    visualQueue = [];
    if (swishGain) {
      swishGain.gain.cancelScheduledValues(ctx.currentTime);
      swishGain.gain.setTargetAtTime(0, ctx.currentTime, 0.03);
    }
    if (swishSrc) swishSrc.stop(ctx.currentTime + 0.2);
    swishSrc = null;
    swishGain = null;
    stopLoop(ctx.currentTime);
    clearPlayhead();
    setDots(null);
    playBtn.textContent = "▶";
    playBtn.classList.remove("on");
    playBtn.setAttribute("aria-pressed", "false");
  }

  playBtn.addEventListener("click", () => (playing ? stop() : start()));

  /* A tune switch mid-playback restarts the form (no fresh count-in): the
     next scheduled beat is beat 1 of bar 1 of the new tune. */
  window.addEventListener("hashchange", () => {
    if (!playing) return;
    barIdx = 0;
    beatInBar = 1;
    beats = beatsPerBar();
    refreshCells();
  });

  /* ------------------------------------------------------- settings menu */

  let dotsEl = null;
  let dotNodes = [];
  let sliderEl = null;
  let numEl = null;

  function setDots(ev) {
    if (!dotsEl || menu.hidden) return;
    const n = ev ? ev.beats : beatsPerBar();
    if (dotNodes.length !== n) {
      dotsEl.innerHTML = "";
      dotNodes = [];
      for (let i = 0; i < n; i++) {
        const d = document.createElement("span");
        d.className = "brush-dot";
        dotsEl.appendChild(d);
        dotNodes.push(d);
      }
    }
    dotNodes.forEach((d, i) => {
      d.classList.toggle("on", !!ev && i === ev.beat - 1);
      d.classList.toggle("count", !!ev && !!ev.count && i === ev.beat - 1);
    });
  }

  function applyBpm(value, fromSlider) {
    bpm = Math.min(BPM_MAX, Math.max(BPM_MIN, Math.round(value) || bpm));
    persist("grilles.brushBpm", bpm);
    bpmBtn.textContent = "♩=" + bpm;
    if (sliderEl && !fromSlider) sliderEl.value = String(bpm);
    if (numEl) {
      numEl.value = String(bpm);
      /* A recording only stretches so far: flag tempos more than ~15% from
         the loop's native BPM (the synth kit has no such limit). */
      const native = source === "loop" && loopMeta && loopMeta[0] ? loopMeta[0].bpm : 0;
      numEl.classList.toggle("warn",
        !!native && (bpm < native * 0.85 || bpm > native * 1.18));
    }
  }

  let sourceHintEl = null;
  function updateSourceHint() {
    if (!sourceHintEl) return;
    if (source === "loop" && loopMeta && loopMeta[0]) {
      const l = loopMeta[0];
      sourceHintEl.innerHTML =
        `<a href="${l.page}" target="_blank" rel="noopener">${l.title}</a>` +
        ` — ${l.author} (${l.license}), sounds best near ${l.bpm} BPM.` +
        " 3/4 tunes use the synth waltz.";
      sourceHintEl.hidden = false;
    } else {
      sourceHintEl.hidden = true;
    }
  }

  function applySource(s) {
    source = s === "loop" ? "loop" : "synth";
    persist("grilles.brushSource", source);
    menu.querySelectorAll(".brush-src button").forEach((b) => {
      b.classList.toggle("on", b.dataset.s === source);
      b.setAttribute("aria-pressed", String(b.dataset.s === source));
    });
    /* The kit/taps pattern choice only applies to the synth source. */
    menu.querySelectorAll(".brush-seg:not(.brush-src) button").forEach((b) => {
      b.disabled = source === "loop";
    });
    if (source === "loop") {
      /* Metadata (not audio) is enough for the hint + tempo range; a missing
         bundle reverts the choice. */
      ensureLoopMeta().then(() => {
        updateSourceHint();
        applyBpm(bpm);
      }).catch((err) => {
        console.error(err);
        applySource("synth");
      });
      /* Mid-take switch: the sweep's last swell would otherwise hold forever. */
      if (playing && swishGain) {
        swishGain.gain.cancelScheduledValues(ctx.currentTime);
        swishGain.gain.setTargetAtTime(0, ctx.currentTime, 0.05);
      }
    }
    updateSourceHint();
    applyBpm(bpm);
  }

  function applyPattern(p) {
    pattern = p === "taps" ? "taps" : "full";
    persist("grilles.brushPattern", pattern);
    menu.querySelectorAll(".brush-seg:not(.brush-src) button").forEach((b) => {
      b.classList.toggle("on", b.dataset.p === pattern);
      b.setAttribute("aria-pressed", String(b.dataset.p === pattern));
    });
    /* Kit → taps while playing: kill the sweep now (its future ramps are
       already cancelled by not scheduling more; flush the queued ones). */
    if (pattern === "taps" && playing && swishGain) {
      swishGain.gain.cancelScheduledValues(ctx.currentTime);
      swishGain.gain.setTargetAtTime(0, ctx.currentTime, 0.05);
    }
  }

  let tapTimes = [];
  function tapTempo() {
    const now = performance.now();
    if (tapTimes.length && now - tapTimes[tapTimes.length - 1] > 2200) tapTimes = [];
    tapTimes.push(now);
    if (tapTimes.length < 2) return;
    const recent = tapTimes.slice(-6);
    const avg = (recent[recent.length - 1] - recent[0]) / (recent.length - 1);
    applyBpm(60000 / avg);
  }

  function buildMenu() {
    menu.innerHTML = "";
    const row = (cls) => {
      const r = document.createElement("div");
      r.className = "brush-row" + (cls ? " " + cls : "");
      menu.appendChild(r);
      return r;
    };

    const tempoRow = row();
    tempoRow.insertAdjacentHTML("beforeend", '<span class="brush-lab">Tempo</span>');
    sliderEl = document.createElement("input");
    sliderEl.type = "range";
    sliderEl.min = String(BPM_MIN);
    sliderEl.max = String(BPM_MAX);
    sliderEl.value = String(bpm);
    sliderEl.setAttribute("aria-label", "Tempo (BPM)");
    sliderEl.addEventListener("input", () => applyBpm(parseInt(sliderEl.value, 10), true));
    numEl = document.createElement("input");
    numEl.type = "number";
    numEl.min = String(BPM_MIN);
    numEl.max = String(BPM_MAX);
    numEl.value = String(bpm);
    numEl.id = "brushBpmNum";
    numEl.setAttribute("aria-label", "Tempo (BPM), editable");
    numEl.addEventListener("change", () => applyBpm(parseFloat(numEl.value)));
    tempoRow.appendChild(sliderEl);
    tempoRow.appendChild(numEl);

    const tapRow = row();
    const tapBtn = document.createElement("button");
    tapBtn.type = "button";
    tapBtn.id = "brushTap";
    tapBtn.textContent = "Tap tempo";
    tapBtn.addEventListener("click", tapTempo);
    tapRow.appendChild(tapBtn);
    dotsEl = document.createElement("div");
    dotsEl.className = "brush-dots";
    tapRow.appendChild(dotsEl);
    dotNodes = [];
    setDots(null);

    const srcRow = row("brush-seg brush-src");
    [["synth", "Synth kit"], ["loop", "Real loop"]].forEach(([s, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.s = s;
      b.textContent = label;
      b.addEventListener("click", () => applySource(s));
      srcRow.appendChild(b);
    });

    const segRow = row("brush-seg");
    [["full", "Brush kit"], ["taps", "Taps only"]].forEach(([p, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.p = p;
      b.textContent = label;
      b.addEventListener("click", () => applyPattern(p));
      segRow.appendChild(b);
    });

    sourceHintEl = row("brush-hint");
    sourceHintEl.hidden = true;

    const volRow = row();
    volRow.insertAdjacentHTML("beforeend", '<span class="brush-lab">Volume</span>');
    const vol = document.createElement("input");
    vol.type = "range";
    vol.min = "0";
    vol.max = "100";
    vol.value = String(Math.round(volume * 100));
    vol.setAttribute("aria-label", "Volume");
    vol.addEventListener("input", () => {
      volume = parseInt(vol.value, 10) / 100;
      persist("grilles.brushVol", volume);
      if (master) master.gain.setTargetAtTime(volume, ctx.currentTime, 0.02);
    });
    volRow.appendChild(vol);

    applyPattern(pattern);
    applySource(source);
  }

  function positionMenu() {
    if (narrowMq.matches) {
      menu.classList.add("sheet");
      menu.style.top = menu.style.left = "";
      return;
    }
    menu.classList.remove("sheet");
    const r = bpmBtn.getBoundingClientRect();
    menu.style.top = r.bottom + 6 + "px";
    const w = menu.offsetWidth;
    menu.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + "px";
  }

  function closeMenu() {
    if (menu.hidden) return;
    menu.hidden = true;
    bpmBtn.setAttribute("aria-expanded", "false");
  }

  bpmBtn.addEventListener("click", (e) => {
    e.stopPropagation(); // keep the outside-click closer from firing
    if (menu.hidden) {
      if (!menu.childElementCount) buildMenu();
      menu.hidden = false;
      bpmBtn.setAttribute("aria-expanded", "true");
      positionMenu();
    } else {
      closeMenu();
    }
  });

  document.addEventListener("click", (e) => {
    if (!e.target.isConnected) return;
    if (!menu.hidden && !menu.contains(e.target) && !bpmBtn.contains(e.target)) closeMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeMenu();
  });

  applyBpm(bpm);
})();
