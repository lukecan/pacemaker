// Deezer client for the Worker. Mirrors pacemaker/providers.py, which was
// checked against the live API: look up by ISRC first, otherwise a plain
// "title artist" search (the advanced track:"..." syntax returns nothing),
// then open the closest plausible hits because search results carry no bpm.

import { normalizeArtist, normalizeTitle, pickBest, plausibleBpm, similarity } from "../site/lib/text.js";

export class OutOfBudget extends Error {}
export class RateLimited extends Error {}

/** Counts outbound requests; the free plan allows 50 per invocation. */
export class Budget {
  constructor(n) { this.left = n; }
  take() {
    if (this.left <= 0) throw new OutOfBudget();
    this.left -= 1;
  }
}

export function parseTrack(it) {
  if (!it || typeof it !== "object" || it.error) return null;
  const artist = it.artist && typeof it.artist === "object" ? it.artist.name || "" : String(it.artist || "");
  const album = it.album && typeof it.album === "object" ? it.album.title || "" : "";
  const d = Number(it.duration);
  return {
    id: it.id != null ? String(it.id) : null,
    title: it.title_short || it.title || "",
    artist,
    album,
    isrc: it.isrc || null,
    bpm: plausibleBpm(it.bpm),
    duration: Number.isFinite(d) && d >= 10 && d <= 1800 ? Math.round(d) : null,
  };
}

export function makeClient(base = "https://api.deezer.com", fetchImpl = fetch) {
  async function get(budget, path) {
    budget.take();
    const res = await fetchImpl(base + path, { headers: { "User-Agent": "pacemaker (+https://github.com/lukecan/pacemaker)" } });
    if (res.status === 429) throw new RateLimited();
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    // Deezer reports its quota as a 200 with error code 4.
    if (data && data.error && Number(data.error.code) === 4) throw new RateLimited();
    return data;
  }

  async function track(budget, ref) {
    const data = await get(budget, `/track/${encodeURIComponent(ref).replace("%3A", ":")}`);
    return parseTrack(data);
  }

  /** {bpm, duration, title, artist, album, isrc, id} or {bpm: null, duration}. */
  async function lookupSong(budget, { title, artist, isrc }) {
    let knownDuration = null;
    if (typeof isrc === "string" && isrc.trim()) {
      const hit = await track(budget, `isrc:${isrc.trim().toUpperCase()}`);
      if (hit && hit.bpm) return hit;
      knownDuration = hit ? hit.duration : null;
    }
    const t = normalizeTitle(title);
    const a = normalizeArtist(artist);
    const q = new URLSearchParams({ q: `${t} ${a}`.trim(), limit: "10" });
    const data = await get(budget, `/search?${q}`);
    const hits = ((data && data.data) || []).map(parseTrack).filter(Boolean);
    const score = (c) => 0.65 * similarity(c.title, t) + 0.35 * similarity(c.artist, a);
    hits.sort((x, y) => score(y) - score(x));
    const plausible = hits.filter((c) => pickBest([{ ...c, bpm: 1 }], t, a));
    if (knownDuration == null && plausible[0]) knownDuration = plausible[0].duration;
    for (const c of plausible.slice(0, 3)) {
      const full = await track(budget, c.id);
      if (full && full.bpm) return { ...full, duration: full.duration || c.duration };
    }
    return { bpm: null, duration: knownDuration };
  }

  async function findArtist(budget, name) {
    const q = new URLSearchParams({ q: name, limit: "5" });
    const data = await get(budget, `/search/artist?${q}`);
    const hits = ((data && data.data) || []).filter((h) => h && h.id);
    if (!hits.length) return null;
    hits.sort((x, y) => similarity(y.name, name) - similarity(x.name, name) || (y.nb_fan || 0) - (x.nb_fan || 0));
    return similarity(hits[0].name, name) >= 0.8 ? { id: String(hits[0].id), name: hits[0].name } : null;
  }

  async function related(budget, artistId) {
    const data = await get(budget, `/artist/${artistId}/related?limit=20`);
    return ((data && data.data) || []).filter((a) => a && a.id).map((a) => ({ id: String(a.id), name: a.name }));
  }

  async function topTracks(budget, artistId, limit = 10) {
    const data = await get(budget, `/artist/${artistId}/top?limit=${limit}`);
    return ((data && data.data) || []).map(parseTrack).filter((t) => t && t.id)
      .map(({ id, title, artist, album, duration }) => ({ id, title, artist, album, duration }));
  }

  return { lookupSong, findArtist, related, topTracks, track };
}
