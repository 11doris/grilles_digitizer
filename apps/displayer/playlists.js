/* Playlist state, localStorage persistence, export/import (spec §11). */
"use strict";

(function (global) {
  const KEY = "grilles.playlists";
  const ACTIVE_KEY = "grilles.activePlaylist";

  /* Validate a parsed playlists document; returns a normalized copy or null.
     Used both for loading from localStorage and for import (§11.5). */
  function sanitize(doc) {
    if (!doc || doc.version !== 1 || !Array.isArray(doc.playlists)) return null;
    const now = new Date().toISOString();
    const playlists = [];
    for (const p of doc.playlists) {
      if (!p || typeof p !== "object") return null;
      if (typeof p.name !== "string" || !Array.isArray(p.tuneIds)) return null;
      if (!p.tuneIds.every((id) => typeof id === "string")) return null;
      playlists.push({
        id: typeof p.id === "string" ? p.id : "",
        name: p.name,
        tuneIds: p.tuneIds.slice(),
        createdAt: typeof p.createdAt === "string" ? p.createdAt : now,
        updatedAt: typeof p.updatedAt === "string" ? p.updatedAt : now,
      });
    }
    return { version: 1, playlists };
  }

  /* Corruption safety (§11.1): a value that fails to parse yields an empty
     set, and the bad value is left in place until the user's first change. */
  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const doc = sanitize(JSON.parse(raw));
        if (doc) return doc;
      }
    } catch (e) { /* corrupt JSON or storage unavailable */ }
    return { version: 1, playlists: [] };
  }

  const doc = load();

  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(doc));
    } catch (e) { /* ignore */ }
  }

  function genId() {
    let id;
    do {
      id = "pl_" + Math.random().toString(36).slice(2, 8);
    } while (doc.playlists.some((p) => p.id === id));
    return id;
  }

  function byId(id) {
    return doc.playlists.find((p) => p.id === id) || null;
  }

  function touch(p) {
    p.updatedAt = new Date().toISOString();
  }

  function create(name, tuneIds) {
    const now = new Date().toISOString();
    const p = {
      id: genId(),
      name: String(name),
      tuneIds: (tuneIds || []).slice(),
      createdAt: now,
      updatedAt: now,
    };
    doc.playlists.push(p);
    save();
    return p;
  }

  function rename(id, name) {
    const p = byId(id);
    if (!p) return;
    p.name = String(name);
    touch(p);
    save();
  }

  function remove(id) {
    const i = doc.playlists.findIndex((p) => p.id === id);
    if (i === -1) return;
    doc.playlists.splice(i, 1);
    save();
  }

  /* A tune appears at most once per playlist — re-adding is a no-op (§11.1). */
  function addTune(id, tuneId) {
    const p = byId(id);
    if (!p || p.tuneIds.includes(tuneId)) return;
    p.tuneIds.push(tuneId);
    touch(p);
    save();
  }

  function removeTune(id, tuneId) {
    const p = byId(id);
    if (!p) return;
    const i = p.tuneIds.indexOf(tuneId);
    if (i === -1) return;
    p.tuneIds.splice(i, 1);
    touch(p);
    save();
  }

  /* Move the tune at index `from` to index `to` within the playlist (§11.3). */
  function moveTune(id, from, to) {
    const p = byId(id);
    if (!p) return;
    if (from < 0 || from >= p.tuneIds.length || to < 0 || to >= p.tuneIds.length) return;
    const [tuneId] = p.tuneIds.splice(from, 1);
    p.tuneIds.splice(to, 0, tuneId);
    touch(p);
    save();
  }

  function getActiveId() {
    try {
      return localStorage.getItem(ACTIVE_KEY);
    } catch (e) {
      return null;
    }
  }

  function setActiveId(id) {
    try {
      if (id) localStorage.setItem(ACTIVE_KEY, id);
      else localStorage.removeItem(ACTIVE_KEY);
    } catch (e) { /* ignore */ }
  }

  function exportJson() {
    return JSON.stringify({ version: 1, playlists: doc.playlists }, null, 2);
  }

  /* Non-destructive merge (§11.5): imported playlists come in as NEW entries
     (fresh ids) — never overwrites or deletes existing ones. Returns
     { added: n } or { error: message } (current playlists untouched on error). */
  function importJson(text) {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      return { error: "not valid JSON" };
    }
    const clean = sanitize(parsed);
    if (!clean) return { error: "not a Grilles playlists file" };
    clean.playlists.forEach((p) => {
      p.id = genId();
      doc.playlists.push(p);
    });
    save();
    return { added: clean.playlists.length };
  }

  global.GrillesPlaylists = {
    all: () => doc.playlists,
    byId,
    create,
    rename,
    remove,
    addTune,
    removeTune,
    moveTune,
    getActiveId,
    setActiveId,
    exportJson,
    importJson,
  };
})(window);
