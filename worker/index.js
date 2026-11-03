// pacemaker Worker: serves the site and a small JSON API.
//
//   POST /api/bpm        {songs: [{key, title, artist, isrc}]}       BPM for library songs
//   POST /api/related    {artists: [name]}                           similar artists
//   POST /api/top        {artists: [{id, name}]}                     their top tracks
//   POST /api/tracks     {ids: [deezerId]}                           BPM for discovered tracks
//   POST /api/playlists  {lang, cadence, minutes, tracks: [{key, match, isNew}]}  share
//   GET  /api/playlists?cadence=170&tolerance=3                      gallery
//   GET  /api/playlists/:slug                                         one playlist
//
// Every lookup is cached in D1 for everyone, so the first person to ask about
// a song pays for it and nobody else does. Each request stays inside the free
// plan's 50 outbound requests; whatever does not fit comes back as "pending"
// and the browser asks again.

import names from "../site/lib/names.json" with { type: "json" };
import { playlistName, playlistSeed } from "../site/lib/names.js";
import { makeKey, norm } from "../site/lib/text.js";
import { Budget, OutOfBudget, RateLimited, makeClient } from "./deezer.js";

const BUDGET = 45;
const LIMITS = { bpm: 25, related: 8, top: 20, tracks: 30 };
const ARTIST_TTL_DAYS = 30;
const MISS_TTL_DAYS = 30;
const SHARES_PER_DAY = 20;

const now = () => new Date().toISOString();
const daysOld = (iso) => (Date.now() - Date.parse(iso)) / 86_400_000;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}
const bad = (message, status = 400) => json({ error: message }, status);

async function body(request) {
  try { return await request.json(); } catch { return null; }
}

function placeholders(n) { return new Array(n).fill("?").join(","); }

async function rowsByKey(db, table, column, keys) {
  if (!keys.length) return new Map();
  const { results } = await db.prepare(
    `SELECT * FROM ${table} WHERE ${column} IN (${placeholders(keys.length)})`).bind(...keys).all();
  return new Map(results.map((r) => [String(r[column]), r]));
}

function saveSong(db, key, hit, fallback) {
  const found = !!(hit && hit.bpm);
  return db.prepare(
    `INSERT INTO songs (key,title,artist,album,isrc,deezer_id,bpm,duration,status,checked_at)
     VALUES (?,?,?,?,?,?,?,?,?,?)
     ON CONFLICT(key) DO UPDATE SET title=excluded.title, artist=excluded.artist, album=excluded.album,
       isrc=excluded.isrc, deezer_id=excluded.deezer_id, bpm=excluded.bpm, duration=excluded.duration,
       status=excluded.status, checked_at=excluded.checked_at`,
  ).bind(
    key,
    (found && hit.title) || fallback.title || null,
    (found && hit.artist) || fallback.artist || null,
    found ? hit.album || null : null,
    (found && hit.isrc) || fallback.isrc || null,
    found ? hit.id : null,
    found ? hit.bpm : null,
    (hit && hit.duration) || null,
    found ? "found" : "missing",
    now(),
  );
}

const songResult = (r) => ({ bpm: r.bpm ?? null, duration: r.duration ?? null, status: r.status });

// ---------------------------------------------------------------- handlers

async function handleBpm(env, client, payload) {
  const songs = Array.isArray(payload?.songs) ? payload.songs.slice(0, LIMITS.bpm) : null;
  if (!songs) return bad("songs must be a list");
  const clean = songs
    .filter((s) => s && typeof s.key === "string" && s.key.length <= 300)
    .map((s) => ({ key: s.key, title: String(s.title || "").slice(0, 300),
      artist: String(s.artist || "").slice(0, 300), isrc: s.isrc ? String(s.isrc).slice(0, 20) : null }));
  const cached = await rowsByKey(env.DB, "songs", "key", clean.map((s) => s.key));
  const results = {};
  const pending = [];
  const writes = [];
  const budget = new Budget(BUDGET);
  let stopped = false;
  for (const s of clean) {
    const row = cached.get(s.key);
    if (row && (row.status === "found" || daysOld(row.checked_at) < MISS_TTL_DAYS)) {
      results[s.key] = songResult(row);
      continue;
    }
    if (stopped) { pending.push(s.key); continue; }
    try {
      const hit = await client.lookupSong(budget, s);
      writes.push(saveSong(env.DB, s.key, hit, s));
      results[s.key] = { bpm: hit.bpm || null, duration: hit.duration || null, status: hit.bpm ? "found" : "missing" };
    } catch (err) {
      if (err instanceof OutOfBudget || err instanceof RateLimited) { stopped = true; pending.push(s.key); }
      else { pending.push(s.key); }
    }
  }
  if (writes.length) await env.DB.batch(writes);
  return json({ results, pending });
}

async function handleRelated(env, client, payload) {
  const list = Array.isArray(payload?.artists) ? payload.artists.slice(0, LIMITS.related) : null;
  if (!list) return bad("artists must be a list");
  const wanted = list.map((n) => String(n || "").slice(0, 200)).filter(Boolean);
  const cached = await rowsByKey(env.DB, "artists", "key", wanted.map(norm));
  const out = [];
  const pending = [];
  const writes = [];
  const budget = new Budget(BUDGET);
  for (const name of wanted) {
    const row = cached.get(norm(name));
    if (row && daysOld(row.checked_at) < ARTIST_TTL_DAYS) {
      out.push({ name, found: !!row.deezer_id, related: JSON.parse(row.related || "[]") });
      continue;
    }
    try {
      const artist = await client.findArtist(budget, name);
      const related = artist ? await client.related(budget, artist.id) : [];
      writes.push(env.DB.prepare(
        `INSERT OR REPLACE INTO artists (key,deezer_id,name,related,checked_at) VALUES (?,?,?,?,?)`,
      ).bind(norm(name), artist ? artist.id : null, artist ? artist.name : name, JSON.stringify(related), now()));
      out.push({ name, found: !!artist, related });
    } catch {
      pending.push(name);
    }
  }
  if (writes.length) await env.DB.batch(writes);
  return json({ artists: out, pending });
}

async function handleTop(env, client, payload) {
  const list = Array.isArray(payload?.artists) ? payload.artists.slice(0, LIMITS.top) : null;
  if (!list) return bad("artists must be a list");
  const ids = list.map((a) => String(a?.id || "")).filter((id) => /^\d{1,15}$/.test(id));
  const cached = await rowsByKey(env.DB, "artist_top", "deezer_id", ids);
  const tracks = [];
  const pending = [];
  const writes = [];
  const budget = new Budget(BUDGET);
  for (const id of ids) {
    const row = cached.get(id);
    if (row && daysOld(row.checked_at) < ARTIST_TTL_DAYS) {
      tracks.push(...JSON.parse(row.tracks).map((t) => ({ ...t, artistId: id })));
      continue;
    }
    try {
      const top = await client.topTracks(budget, id, 10);
      writes.push(env.DB.prepare(
        `INSERT OR REPLACE INTO artist_top (deezer_id,tracks,checked_at) VALUES (?,?,?)`,
      ).bind(id, JSON.stringify(top), now()));
      tracks.push(...top.map((t) => ({ ...t, artistId: id })));
    } catch {
      pending.push(id);
    }
  }
  if (writes.length) await env.DB.batch(writes);
  return json({ tracks, pending });
}

async function handleTracks(env, client, payload) {
  const list = Array.isArray(payload?.ids) ? payload.ids.slice(0, LIMITS.tracks) : null;
  if (!list) return bad("ids must be a list");
  const ids = list.map(String).filter((id) => /^\d{1,15}$/.test(id));
  const cached = await rowsByKey(env.DB, "songs", "deezer_id", ids);
  const out = {};
  const pending = [];
  const writes = [];
  const budget = new Budget(BUDGET);
  let stopped = false;
  for (const id of ids) {
    const row = cached.get(id);
    if (row) {
      out[id] = { key: row.key, title: row.title, artist: row.artist, album: row.album,
        isrc: row.isrc, bpm: row.bpm, duration: row.duration, status: row.status };
      continue;
    }
    if (stopped) { pending.push(id); continue; }
    try {
      const t = await client.track(budget, id);
      if (!t) { out[id] = { status: "missing" }; continue; }
      const key = makeKey(t.title, t.artist, t.isrc);
      // Store under the Deezer id too, so a miss is remembered.
      writes.push(env.DB.prepare(
        `INSERT INTO songs (key,title,artist,album,isrc,deezer_id,bpm,duration,status,checked_at)
         VALUES (?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT(key) DO UPDATE SET deezer_id=COALESCE(songs.deezer_id, excluded.deezer_id),
           bpm=COALESCE(songs.bpm, excluded.bpm), duration=COALESCE(songs.duration, excluded.duration),
           status=CASE WHEN songs.status='found' THEN 'found' ELSE excluded.status END`,
      ).bind(key, t.title, t.artist, t.album, t.isrc, id, t.bpm, t.duration, t.bpm ? "found" : "missing", now()));
      out[id] = { key, title: t.title, artist: t.artist, album: t.album, isrc: t.isrc,
        bpm: t.bpm, duration: t.duration, status: t.bpm ? "found" : "missing" };
    } catch (err) {
      pending.push(id);
      if (err instanceof OutOfBudget || err instanceof RateLimited) stopped = true;
    }
  }
  if (writes.length) await env.DB.batch(writes);
  return json({ tracks: out, pending });
}

function randomSlug() {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(7));
  return [...bytes].map((b) => alphabet[b % alphabet.length]).join("");
}

async function handleShare(env, request, payload) {
  const lang = payload?.lang === "tr" ? "tr" : "en";
  const cadence = Number(payload?.cadence);
  const minutes = Math.round(Number(payload?.minutes));
  const items = Array.isArray(payload?.tracks) ? payload.tracks.slice(0, 40) : null;
  if (!items || !Number.isFinite(cadence) || cadence < 120 || cadence > 220) return bad("invalid playlist");
  if (!Number.isFinite(minutes) || minutes < 10 || minutes > 180) return bad("invalid length");

  // Only songs the cache resolved through Deezer can be shared, and their text
  // comes from the cache, so nobody can publish arbitrary titles.
  const keys = items.map((t) => String(t?.key || "")).filter(Boolean);
  const rows = await rowsByKey(env.DB, "songs", "key", keys);
  const tracks = [];
  for (const item of items) {
    const row = rows.get(String(item.key));
    if (!row || row.status !== "found" || row.bpm == null) continue;
    tracks.push({ key: row.key, title: row.title, artist: row.artist, album: row.album || "",
      isrc: row.isrc || "", bpm: row.bpm, duration: row.duration,
      match: item.match === "half-time" ? "half-time" : "direct", isNew: !!item.isNew });
  }
  if (tracks.length < 5) return bad("not enough recognised songs to share", 422);

  const client = request.headers.get("cf-connecting-ip") || "local";
  const bucket = `${client}:${now().slice(0, 10)}`;
  const quota = await env.DB.prepare(`SELECT n FROM share_quota WHERE bucket = ?`).bind(bucket).first();
  if (quota && quota.n >= SHARES_PER_DAY) return bad("daily share limit reached", 429);

  const name = playlistName(playlistSeed(tracks.map((t) => t.key), cadence), lang, names);
  const slug = randomSlug();
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO playlists (slug,name,lang,cadence,minutes,new_count,tracks,created_at) VALUES (?,?,?,?,?,?,?,?)`,
    ).bind(slug, name, lang, cadence, minutes, tracks.filter((t) => t.isNew).length, JSON.stringify(tracks), now()),
    env.DB.prepare(
      `INSERT INTO share_quota (bucket,n) VALUES (?,1) ON CONFLICT(bucket) DO UPDATE SET n = n + 1`,
    ).bind(bucket),
  ]);
  return json({ slug, name }, 201);
}

async function handleGallery(env, url) {
  const cadence = Number(url.searchParams.get("cadence"));
  const tolerance = Math.min(Math.max(Number(url.searchParams.get("tolerance")) || 3, 0), 20);
  const limit = Math.min(Math.max(Number(url.searchParams.get("limit")) || 24, 1), 60);
  let stmt;
  if (Number.isFinite(cadence) && cadence > 0) {
    stmt = env.DB.prepare(
      `SELECT slug,name,lang,cadence,minutes,new_count,json_array_length(tracks) AS count,created_at
       FROM playlists WHERE cadence BETWEEN ? AND ? ORDER BY created_at DESC LIMIT ?`,
    ).bind(cadence - tolerance, cadence + tolerance, limit);
  } else {
    stmt = env.DB.prepare(
      `SELECT slug,name,lang,cadence,minutes,new_count,json_array_length(tracks) AS count,created_at
       FROM playlists ORDER BY created_at DESC LIMIT ?`,
    ).bind(limit);
  }
  const { results } = await stmt.all();
  return json({ playlists: results });
}

async function handleOne(env, slug) {
  if (!/^[a-z2-9]{7}$/.test(slug)) return bad("not found", 404);
  const row = await env.DB.prepare(`SELECT * FROM playlists WHERE slug = ?`).bind(slug).first();
  if (!row) return bad("not found", 404);
  return json({ ...row, tracks: JSON.parse(row.tracks) });
}

// -------------------------------------------------------------------- CORS
// The page may be served from another site (mertcangenc.com/pacemaker/), so
// browsers there need permission to call this API. Only listed origins get it.

const DEFAULT_ORIGINS = "https://mertcangenc.com,https://www.mertcangenc.com";

function corsHeaders(request, env) {
  const origin = request.headers.get("origin");
  if (!origin) return null;
  const allowed = String(env.ALLOWED_ORIGINS || DEFAULT_ORIGINS).split(",").map((o) => o.trim()).filter(Boolean);
  if (!allowed.includes(origin)) return null;
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    "access-control-max-age": "86400",
    vary: "origin",
  };
}

function withCors(response, cors) {
  if (!cors) return response;
  const out = new Response(response.body, response);
  for (const [k, v] of Object.entries(cors)) out.headers.set(k, v);
  return out;
}

// ------------------------------------------------------------------ router

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(request);
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") {
      return cors ? new Response(null, { status: 204, headers: cors }) : new Response(null, { status: 403 });
    }
    return withCors(await route(request, env, url), cors);
  },
};

async function route(request, env, url) {
  {
    const client = makeClient(env.DEEZER_BASE || "https://api.deezer.com");
    try {
      if (request.method === "POST") {
        const payload = await body(request);
        if (!payload) return bad("expected a JSON body");
        switch (url.pathname) {
          case "/api/bpm": return await handleBpm(env, client, payload);
          case "/api/related": return await handleRelated(env, client, payload);
          case "/api/top": return await handleTop(env, client, payload);
          case "/api/tracks": return await handleTracks(env, client, payload);
          case "/api/playlists": return await handleShare(env, request, payload);
        }
      } else if (request.method === "GET") {
        if (url.pathname === "/api/playlists") return await handleGallery(env, url);
        const m = url.pathname.match(/^\/api\/playlists\/([^/]+)$/);
        if (m) return await handleOne(env, m[1]);
        if (url.pathname === "/api/health") return json({ ok: true });
      }
      return bad("not found", 404);
    } catch (err) {
      return bad(`server error: ${err && err.message ? err.message : "unknown"}`, 500);
    }
  }
}
