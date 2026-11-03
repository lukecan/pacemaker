// Builds a playlist in the browser. Only song names travel to the API; the
// library itself never leaves the page.

import { rankSimilar, seedArtists } from "./discovery.js";
import { assemble, matchTracks, shuffle, DEFAULT_TRACK_SECONDS } from "./match.js";
import { playlistName, playlistSeed } from "./names.js";
import { norm, normalizeArtist, normalizeTitle, seedName } from "./text.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function makeApi(fetchImpl = fetch, base = "") {
  async function post(path, data) {
    const res = await fetchImpl(`${base}${path}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`api ${path} ${res.status}`);
    return res.json();
  }
  return {
    bpm: (songs) => post("/api/bpm", { songs }),
    related: (artists) => post("/api/related", { artists }),
    top: (artists) => post("/api/top", { artists }),
    tracks: (ids) => post("/api/tracks", { ids }),
    share: (data) => post("/api/playlists", data),
    like: (slug, like = true) => post(`/api/playlists/${encodeURIComponent(slug)}/like`, { like }),
    async gallery(cadence, tolerance = 3, sort = "top") {
      const q = new URLSearchParams({ tolerance: String(tolerance), sort });
      if (cadence) q.set("cadence", String(cadence));
      const res = await fetchImpl(`${base}/api/playlists?${q}`);
      if (!res.ok) throw new Error("gallery");
      return res.json();
    },
    async playlist(slug) {
      const res = await fetchImpl(`${base}/api/playlists/${encodeURIComponent(slug)}`);
      if (!res.ok) throw new Error(res.status === 404 ? "not-found" : "playlist");
      return res.json();
    },
  };
}

function chunks(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/** Run `fn` over chunks, `parallel` at a time; items reported pending are retried. */
async function drain(items, size, parallel, fn, { enough = () => false, retries = 3, pause = 1500 } = {}) {
  let queue = items.slice();
  let round = 0;
  while (queue.length && round <= retries) {
    const retry = [];
    const groups = chunks(queue, size);
    for (let i = 0; i < groups.length; i += parallel) {
      if (enough()) return;
      const batch = groups.slice(i, i + parallel);
      const results = await Promise.all(batch.map((g) => fn(g).catch(() => g)));
      for (const pending of results) if (pending && pending.length) retry.push(...pending);
    }
    if (enough()) return;
    queue = retry;
    round++;
    if (queue.length) await sleep(pause * round);
  }
}

export async function buildPlaylist({
  songs, cadence, minutes = 60, fresh = 0.3, tolerance = 3, halfTime = false, served = {}, seed = Date.now() % 2 ** 31,
  api, names, lang = "en", onProgress = () => {}, maxLookups = 900, pause = 1500,
}) {
  const perHour = (minutes * 60) / DEFAULT_TRACK_SECONDS;
  const needKnown = Math.max(8, Math.ceil(perHour * (1 - fresh) * 2.5));
  const needNew = fresh > 0 ? Math.max(4, Math.ceil(perHour * fresh * 2.5)) : 0;

  // ---- 1. your library: check tempos until there are enough to choose from
  const library = songs.map((s) => ({ ...s, origin: "library" }));
  const byKey = new Map(library.map((s) => [s.key, s]));
  // Half-time (two steps per beat) is opt-in: off, only direct tempo matches count.
  const fits = (s) => matchTracks([s], cadence, tolerance, { halfTime }).length > 0;
  let known = library.filter((s) => s.bpm && fits(s)).length;
  const unknown = shuffle(library.filter((s) => !s.bpm), seed)
    .sort((a, b) => (served[a.key] || 0) - (served[b.key] || 0))
    .slice(0, maxLookups);
  let checked = 0;
  onProgress({ phase: "library", checked, total: unknown.length, matches: known });
  // 15 per call: a search-path lookup costs 2-4 Deezer requests and the Worker
  // has 45 per call, so a full batch fits and every answer gets cached.
  await drain(unknown, 15, 2, async (group) => {
    const res = await api.bpm(group.map(({ key, title, artist, isrc }) => ({ key, title, artist, isrc })));
    for (const [key, r] of Object.entries(res.results || {})) {
      const s = byKey.get(key);
      if (!s) continue;
      s.bpm = r.bpm;
      s.duration ||= r.duration;
      checked++;
      if (s.bpm && fits(s)) known++;
    }
    onProgress({ phase: "library", checked, total: unknown.length, matches: known });
    return group.filter((g) => (res.pending || []).includes(g.key));
  }, { enough: () => known >= needKnown, pause });

  // ---- 2. discovery: songs you do not have, by artists like yours
  const discovered = [];
  if (needNew) {
    onProgress({ phase: "artists", checked: 0, total: 0, matches: 0 });
    const seeds = seedArtists(library, 12);
    const relatedBySeed = {};
    await drain(seeds.map((s) => s.name), 8, 1, async (group) => {
      const res = await api.related(group);
      for (const a of res.artists || []) relatedBySeed[a.name] = a.related || [];
      return res.pending || [];
    }, { pause });
    const ownedArtists = new Set(library.map((s) => norm(seedName(s.artist))));
    const ranked = rankSimilar(seeds, relatedBySeed, ownedArtists).slice(0, 40);

    const pool = [];
    await drain(ranked.map((a) => ({ id: a.id, name: a.name })), 20, 1, async (group) => {
      const res = await api.top(group);
      pool.push(...(res.tracks || []));
      return group.filter((g) => (res.pending || []).includes(String(g.id)));
    }, { pause });

    // Round-robin over artists, best-ranked first, so one band cannot take over.
    const ownedSongs = new Set(library.map((s) => `${norm(normalizeTitle(s.title))}|${norm(normalizeArtist(s.artist))}`));
    const rank = new Map(ranked.map((a, i) => [String(a.id), i]));
    const byArtist = new Map();
    for (const t of shuffle(pool, seed + 1)) {
      if (ownedSongs.has(`${norm(normalizeTitle(t.title))}|${norm(normalizeArtist(t.artist))}`)) continue;
      const k = String(t.artistId);
      if (!byArtist.has(k)) byArtist.set(k, []);
      byArtist.get(k).push(t);
    }
    const lanes = [...byArtist.entries()].sort((a, b) => (rank.get(a[0]) ?? 99) - (rank.get(b[0]) ?? 99)).map(([, v]) => v);
    const ordered = [];
    for (let round = 0; lanes.some((l) => l.length); round++) for (const lane of lanes) if (lane.length) ordered.push(lane.shift());

    let found = 0;
    let looked = 0;
    const seen = new Set();
    onProgress({ phase: "discover", checked: 0, total: ordered.length, matches: 0 });
    await drain(ordered.map((t) => String(t.id)), 30, 1, async (group) => {
      const res = await api.tracks(group);
      for (const [, t] of Object.entries(res.tracks || {})) {
        looked++;
        if (!t || t.status !== "found" || !t.key || byKey.has(t.key) || seen.has(t.key)) continue;
        seen.add(t.key);
        const song = { key: t.key, title: t.title, artist: t.artist, album: t.album || "", isrc: t.isrc || null,
          bpm: t.bpm, duration: t.duration, favorite: false, playlists: [], origin: "discovery" };
        discovered.push(song);
        if (fits(song)) found++;
      }
      onProgress({ phase: "discover", checked: looked, total: ordered.length, matches: found });
      return res.pending || [];
    }, { enough: () => found >= needNew, pause });
  }

  // ---- 3. assemble
  const candidates = matchTracks([...library, ...discovered], cadence, tolerance, { halfTime });
  const playlist = assemble(candidates, { minutes, fresh, seed, served });
  const name = playlistName(playlistSeed(playlist.map((t) => t.key), cadence), lang, names);
  onProgress({ phase: "done" });
  return {
    name, playlist,
    stats: {
      libraryChecked: checked, libraryMatches: known,
      discovered: discovered.length, newMatches: candidates.filter((c) => c.origin === "discovery").length,
    },
  };
}

/** Same columns as a TuneMyMusic library export, so it imports straight back. */
export function transferRows(playlist, name) {
  return playlist.map((t) => ({
    "Track name": t.title, "Artist name": t.artist, "Album": t.album || "",
    "Playlist name": name, "Type": "Playlist", "ISRC": t.isrc || "",
  }));
}
export const TRANSFER_COLUMNS = ["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC"];
