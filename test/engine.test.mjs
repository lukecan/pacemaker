import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import { buildPlaylist, makeApi, transferRows } from "../site/lib/engine.js";
import worker from "../worker/index.js";
import { makeD1 } from "./d1.mjs";

const names = JSON.parse(fs.readFileSync(new URL("../site/lib/names.json", import.meta.url), "utf8"));

// A fake Deezer that knows every song, with a tempo derived from its name.
function fakeDeezer() {
  const hash = (s) => [...s].reduce((h, c) => (Math.imul(h, 31) + c.codePointAt(0)) >>> 0, 7);
  const tempo = (s) => [170, 85, 120, 140, 100, 171, 168, 150][hash(s) % 8];
  let calls = 0;
  const fetchImpl = async (url) => {
    calls++;
    const u = new URL(url);
    const p = u.pathname;
    if (p === "/search") {
      const [title, ...artist] = u.searchParams.get("q").split(" ");
      return Response.json({ data: [{ id: hash(u.searchParams.get("q")) % 1e9, title_short: title, artist: { name: artist.join(" ") }, duration: 210 }] });
    }
    if (p === "/search/artist") return Response.json({ data: [{ id: hash(u.searchParams.get("q")) % 1e6, name: u.searchParams.get("q") }] });
    let m = p.match(/^\/artist\/(\d+)\/related$/);
    if (m) return Response.json({ data: Array.from({ length: 6 }, (_, i) => ({ id: Number(m[1]) * 10 + i, name: `Similar ${m[1]}-${i}` })) });
    m = p.match(/^\/artist\/(\d+)\/top$/);
    if (m) return Response.json({ data: Array.from({ length: 10 }, (_, i) => ({ id: Number(m[1]) * 100 + i, title_short: `Hit ${i} of ${m[1]}`, artist: { name: `Similar ${m[1]}` }, duration: 200 })) });
    m = p.match(/^\/track\/(\d+)$/);
    if (m) return Response.json({ id: Number(m[1]), title_short: `Track ${m[1]}`, artist: { name: `Artist ${Math.floor(m[1] / 100)}` }, bpm: tempo(m[1]), duration: 205, isrc: `ZZ${m[1]}` });
    m = p.match(/^\/track\/isrc:(.+)$/);
    if (m) return Response.json({ error: { code: 800 } });
    throw new Error(url);
  };
  return { fetchImpl, calls: () => calls };
}

function library(n) {
  return Array.from({ length: n }, (_, i) => ({
    key: `song${i}|band${i % 25}`, title: `Song${i}`, artist: `Band${i % 25}`, album: "", isrc: null,
    bpm: null, duration: 220, favorite: i % 3 === 0, playlists: i % 50 === 0 ? ["Replay 2026"] : [],
  }));
}

function wire() {
  const deezer = fakeDeezer();
  globalThis.fetch = deezer.fetchImpl;
  const env = { DB: makeD1(), DEEZER_BASE: "https://deezer.test", ASSETS: { fetch: () => new Response("") } };
  const api = makeApi((path, init) => worker.fetch(new Request(`https://p.test${path}`, init), env));
  return { api, deezer, env };
}

test("builds an hour with the asked share of new songs, stopping early", async () => {
  const { api } = wire();
  const phases = new Set();
  const result = await buildPlaylist({ songs: library(1500), cadence: 170, minutes: 60, fresh: 0.3, seed: 4,
    api, names, lang: "en", pause: 0, onProgress: (p) => phases.add(p.phase) });
  const secs = result.playlist.reduce((a, t) => a + (t.duration || 210), 0);
  assert.ok(Math.abs(secs - 3600) <= 120, `length ${secs}s`);
  const newShare = result.playlist.filter((t) => t.origin === "discovery").length / result.playlist.length;
  assert.ok(newShare >= 0.2 && newShare <= 0.4, `new share ${newShare}`);
  assert.ok(result.stats.libraryChecked < 1500, "should stop once there is enough to choose from");
  assert.deepEqual([...phases], ["library", "artists", "discover", "done"]);
  assert.match(result.name, / · .+ No\. \d+$/);
});

test("a second visitor with an overlapping library is served from the cache", async () => {
  const { api, deezer } = wire();
  const songs = library(400);
  await buildPlaylist({ songs, cadence: 170, minutes: 30, fresh: 0, seed: 1, api, names, pause: 0, maxLookups: 400 });
  const after = deezer.calls();
  await buildPlaylist({ songs, cadence: 170, minutes: 30, fresh: 0, seed: 1, api, names, pause: 0, maxLookups: 400 });
  assert.equal(deezer.calls(), after);
});

test("half-time songs only when asked", async () => {
  const { api } = wire();
  const off = await buildPlaylist({ songs: library(800), cadence: 170, minutes: 45, fresh: 0.3, seed: 2, api, names, pause: 0 });
  assert.equal(off.playlist.some((t) => t.match === "half-time"), false);
  assert.ok(off.playlist.every((t) => Math.abs(t.bpm - 170) <= 3));
  const on = await buildPlaylist({ songs: library(800), cadence: 170, minutes: 45, fresh: 0.3, seed: 2, api, names, pause: 0, halfTime: true });
  assert.ok(on.playlist.some((t) => t.match === "half-time"));
});

test("no new songs when fresh is 0", async () => {
  const { api } = wire();
  const r = await buildPlaylist({ songs: library(600), cadence: 170, minutes: 45, fresh: 0, api, names, pause: 0 });
  assert.equal(r.playlist.some((t) => t.origin === "discovery"), false);
});

test("transfer rows use TuneMyMusic's own export columns", () => {
  const rows = transferRows([{ title: "Rehab", artist: "Amy Winehouse", album: "B2B", isrc: "GBUM70603730" }], "Sole Music · Golden Hour No. 3");
  assert.deepEqual(Object.keys(rows[0]), ["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC"]);
  assert.equal(rows[0].Type, "Playlist");
});
