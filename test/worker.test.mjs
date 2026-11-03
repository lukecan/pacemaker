import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import worker from "../worker/index.js";
import { makeD1 } from "./d1.mjs";

// Recorded from api.deezer.com on 2026-11-03 (trimmed).
const TRACKS = {
  "isrc:GBUM70603730": { id: 2176852, title_short: "Rehab", bpm: 145.6, duration: 212, isrc: "GBUM70603730", artist: { name: "Amy Winehouse" }, album: { title: "Back to Black" } },
  "isrc:USIR20500155": { id: 689411472, title_short: "Mr. Brightside", bpm: 0, duration: 279, artist: { name: "The Killers" } },
  1079668: { id: 1079668, title_short: "Africa", bpm: 92.49, duration: 296, isrc: "USSM19801941", artist: { name: "Toto" }, album: { title: "Toto IV" } },
  3157689: { id: 3157689, title_short: "Aerodynamite", bpm: 122, duration: 468, artist: { name: "Daft Punk" } },
  3089386: { id: 3089386, title_short: "Aerodynamic", bpm: 123.4, duration: 212, isrc: "GBDUW0000057", artist: { name: "Daft Punk" } },
  14633829: { id: 14633829, title_short: "505", bpm: 140.2, duration: 253, isrc: "GBCEL0700074", artist: { name: "Arctic Monkeys" } },
  70322130: { id: 70322130, title_short: "Do I Wanna Know?", bpm: 85, duration: 272, isrc: "GBCEL1300362", artist: { name: "Arctic Monkeys" } },
};
const SEARCH = {
  "Africa Toto": [{ id: 1079668, title_short: "Africa", duration: 296, artist: { name: "Toto" } }],
  "Aerodynamic Daft Punk": [
    { id: 3157689, title_short: "Aerodynamite", duration: 468, artist: { name: "Daft Punk" } },
    { id: 3089386, title_short: "Aerodynamic", duration: 212, artist: { name: "Daft Punk" } },
  ],
};
const ARTISTS = { "The Strokes": [{ id: 569, name: "The Strokes", nb_fan: 1759344 }, { id: 11, name: "The Rolling Stones", nb_fan: 7120558 }] };
const RELATED = { 569: [{ id: 1182, name: "Arctic Monkeys" }, { id: 1652, name: "Interpol" }] };
const TOP = { 1182: [{ id: 14633829, title_short: "505", duration: 253, artist: { name: "Arctic Monkeys" }, album: { title: "FWN" } },
  { id: 70322130, title_short: "Do I Wanna Know?", duration: 272, artist: { name: "Arctic Monkeys" }, album: { title: "AM" } }] };

let calls;
let quotaAfter;
beforeEach(() => {
  calls = [];
  quotaAfter = Infinity;
  globalThis.fetch = async (url) => {
    calls.push(url);
    if (calls.length > quotaAfter) return Response.json({ error: { type: "Exception", message: "Quota limit exceeded", code: 4 } });
    const u = new URL(url);
    const p = u.pathname;
    if (p === "/search") return Response.json({ data: SEARCH[u.searchParams.get("q")] || [] });
    if (p === "/search/artist") return Response.json({ data: ARTISTS[u.searchParams.get("q")] || [] });
    let m = p.match(/^\/artist\/(\d+)\/related$/);
    if (m) return Response.json({ data: RELATED[m[1]] || [] });
    m = p.match(/^\/artist\/(\d+)\/top$/);
    if (m) return Response.json({ data: TOP[m[1]] || [] });
    m = p.match(/^\/track\/(.+)$/);
    if (m) return Response.json(TRACKS[decodeURIComponent(m[1])] || { error: { code: 800 } });
    throw new Error(`unexpected ${url}`);
  };
});

const env = () => ({ DB: makeD1(), DEEZER_BASE: "https://deezer.test", ASSETS: { fetch: () => new Response("asset") } });
const post = (e, path, data, ip = "1.2.3.4") => worker.fetch(new Request(`https://p.test${path}`, {
  method: "POST", body: JSON.stringify(data), headers: { "content-type": "application/json", "cf-connecting-ip": ip } }), e);
const get = (e, path) => worker.fetch(new Request(`https://p.test${path}`), e);

test("BPM by ISRC, by search, and misses are cached for everyone", async () => {
  const e = env();
  const songs = [
    { key: "GBUM70603730", title: "Rehab", artist: "Amy Winehouse", isrc: "GBUM70603730" },
    { key: "africa|toto", title: "Africa", artist: "Toto" },
    { key: "aerodynamic|daft punk", title: "Aerodynamic", artist: "Daft Punk" },
    { key: "USIR20500155", title: "Mr. Brightside", artist: "The Killers", isrc: "USIR20500155" },
  ];
  const first = await (await post(e, "/api/bpm", { songs })).json();
  assert.equal(first.results.GBUM70603730.bpm, 145.6);
  assert.equal(first.results["africa|toto"].bpm, 92.49);
  assert.equal(first.results["aerodynamic|daft punk"].bpm, 123.4); // not "Aerodynamite"
  assert.equal(first.results.USIR20500155.status, "missing");
  assert.deepEqual(first.pending, []);

  const before = calls.length;
  const again = await (await post(e, "/api/bpm", { songs })).json();
  assert.equal(calls.length, before, "second ask must not reach Deezer");
  assert.equal(again.results.GBUM70603730.bpm, 145.6);
});

test("Deezer quota errors come back as pending instead of misses", async () => {
  const e = env();
  quotaAfter = 0;
  const r = await (await post(e, "/api/bpm", { songs: [{ key: "GBUM70603730", title: "Rehab", artist: "Amy", isrc: "GBUM70603730" }] })).json();
  assert.deepEqual(r.pending, ["GBUM70603730"]);
  const row = await e.DB.prepare("SELECT * FROM songs").first();
  assert.equal(row, null, "a quota error must not be cached as a miss");
});

test("the per-call budget stays under the free plan's 50 requests", async () => {
  const e = env();
  const songs = Array.from({ length: 25 }, (_, i) => ({ key: `nope${i}|x`, title: `Nope ${i}`, artist: "X" }));
  const r = await (await post(e, "/api/bpm", { songs })).json();
  assert.ok(calls.length <= 45, `made ${calls.length} requests`);
  assert.equal(Object.keys(r.results).length + r.pending.length, 25);
});

test("discovery chain: related artists, top tracks, then track BPM", async () => {
  const e = env();
  const rel = await (await post(e, "/api/related", { artists: ["The Strokes"] })).json();
  assert.deepEqual(rel.artists[0].related.map((a) => a.name), ["Arctic Monkeys", "Interpol"]);
  const top = await (await post(e, "/api/top", { artists: [{ id: "1182", name: "Arctic Monkeys" }] })).json();
  assert.deepEqual(top.tracks.map((t) => t.title), ["505", "Do I Wanna Know?"]);
  const tr = await (await post(e, "/api/tracks", { ids: top.tracks.map((t) => t.id) })).json();
  assert.equal(tr.tracks["70322130"].bpm, 85);
  assert.equal(tr.tracks["70322130"].key, "GBCEL1300362");
  const n = calls.length;
  await post(e, "/api/related", { artists: ["The Strokes"] });
  await post(e, "/api/top", { artists: [{ id: "1182" }] });
  await post(e, "/api/tracks", { ids: ["70322130"] });
  assert.equal(calls.length, n, "repeat discovery is served from the cache");
});

test("sharing uses cached Deezer metadata, never the uploaded text", async () => {
  const e = env();
  const ids = ["14633829", "70322130", "1079668", "3089386"];
  await post(e, "/api/tracks", { ids });
  await post(e, "/api/bpm", { songs: [{ key: "GBUM70603730", title: "Rehab", artist: "Amy", isrc: "GBUM70603730" }] });
  const keys = ["GBCEL0700074", "GBCEL1300362", "USSM19801941", "GBDUW0000057", "GBUM70603730"];
  const res = await post(e, "/api/playlists", { lang: "tr", cadence: 170, minutes: 60,
    tracks: [...keys.map((key) => ({ key, match: "direct", title: "SPAM http://evil" })), { key: "unknown|x" }] });
  assert.equal(res.status, 201);
  const { slug, name } = await res.json();
  assert.match(name, / · .+ No\. \d+$/);
  const one = await (await get(e, `/api/playlists/${slug}`)).json();
  assert.equal(one.tracks.length, 5);
  assert.ok(one.tracks.every((t) => !t.title.includes("SPAM")));
  const gallery = await (await get(e, "/api/playlists?cadence=171&tolerance=3")).json();
  assert.equal(gallery.playlists[0].slug, slug);
  assert.equal(gallery.playlists[0].count, 5);
  const none = await (await get(e, "/api/playlists?cadence=150&tolerance=3")).json();
  assert.equal(none.playlists.length, 0);
});

test("sharing needs at least five recognised songs and is rate limited", async () => {
  const e = env();
  const r = await post(e, "/api/playlists", { lang: "en", cadence: 170, minutes: 60, tracks: [{ key: "x" }] });
  assert.equal(r.status, 422);
  const ids = ["14633829", "70322130", "1079668", "3089386", "3157689"];
  await post(e, "/api/tracks", { ids });
  const keys = (await e.DB.prepare("SELECT key FROM songs WHERE status='found'").all()).results.map((x) => x.key);
  for (let i = 0; i < 20; i++) {
    assert.equal((await post(e, "/api/playlists", { lang: "en", cadence: 170, minutes: 60, tracks: keys.map((key) => ({ key })) }, "9.9.9.9")).status, 201);
  }
  assert.equal((await post(e, "/api/playlists", { lang: "en", cadence: 170, minutes: 60, tracks: keys.map((key) => ({ key })) }, "9.9.9.9")).status, 429);
});

test("bad input is refused without touching Deezer", async () => {
  const e = env();
  assert.equal((await post(e, "/api/bpm", { songs: "nope" })).status, 400);
  assert.equal((await post(e, "/api/playlists", { cadence: 999, minutes: 60, tracks: [] })).status, 400);
  assert.equal((await get(e, "/api/playlists/NOT-A-SLUG")).status, 404);
  assert.equal((await get(e, "/api/playlists/abcdefg")).status, 404);
  assert.equal(calls.length, 0);
  assert.equal(await (await get(e, "/somewhere")).text(), "asset");
});


test("CORS: listed origins may call the API, others get no permission", async () => {
  const e = env();
  const preflight = (origin) => worker.fetch(new Request("https://p.test/api/bpm", {
    method: "OPTIONS", headers: { origin, "access-control-request-method": "POST" } }), e);
  const ok = await preflight("https://mertcangenc.com");
  assert.equal(ok.status, 204);
  assert.equal(ok.headers.get("access-control-allow-origin"), "https://mertcangenc.com");
  assert.match(ok.headers.get("access-control-allow-headers"), /content-type/);
  assert.equal((await preflight("https://evil.example")).status, 403);

  const res = await worker.fetch(new Request("https://p.test/api/playlists", { headers: { origin: "https://www.mertcangenc.com" } }), e);
  assert.equal(res.headers.get("access-control-allow-origin"), "https://www.mertcangenc.com");
  const other = await worker.fetch(new Request("https://p.test/api/playlists", { headers: { origin: "https://evil.example" } }), e);
  assert.equal(other.headers.get("access-control-allow-origin"), null);
  const same = await get(e, "/api/health");
  assert.equal(same.headers.get("access-control-allow-origin"), null);

  const custom = { ...e, ALLOWED_ORIGINS: "http://127.0.0.1:8800" };
  const local = await worker.fetch(new Request("https://p.test/api/bpm", {
    method: "OPTIONS", headers: { origin: "http://127.0.0.1:8800" } }), custom);
  assert.equal(local.status, 204);
});


async function sharedPlaylist(e, ip = "1.1.1.1") {
  await post(e, "/api/tracks", { ids: ["14633829", "70322130", "1079668", "3089386", "3157689"] });
  const keys = (await e.DB.prepare("SELECT key FROM songs WHERE status='found'").all()).results.map((x) => x.key);
  return (await (await post(e, "/api/playlists", { lang: "en", cadence: 170, minutes: 60, tracks: keys.map((key) => ({ key })) }, ip)).json()).slug;
}

test("likes: one per visitor, can be taken back, unknown playlists refused", async () => {
  const e = env();
  const slug = await sharedPlaylist(e);
  const like = (ip, body = {}) => post(e, `/api/playlists/${slug}/like`, body, ip);
  assert.deepEqual(await (await like("5.5.5.5")).json(), { likes: 1, liked: true });
  assert.deepEqual(await (await like("5.5.5.5")).json(), { likes: 1, liked: true }, "same visitor again");
  assert.deepEqual(await (await like("6.6.6.6")).json(), { likes: 2, liked: true });
  assert.deepEqual(await (await like("5.5.5.5", { like: false })).json(), { likes: 1, liked: false });
  assert.equal((await post(e, "/api/playlists/abcdefg/like", {})).status, 404);
  const stored = (await e.DB.prepare("SELECT voter FROM likes").all()).results.map((r) => r.voter);
  assert.ok(stored.every((v) => /^[0-9a-f]{64}$/.test(v) && !v.includes("6.6.6.6")), "only hashes are stored");
  assert.equal((await (await get(e, `/api/playlists/${slug}`)).json()).likes, 1);
});

test("gallery puts the most liked first, or the newest when asked", async () => {
  const e = env();
  const a = await sharedPlaylist(e, "1.1.1.1");
  await new Promise((r) => setTimeout(r, 5));
  const b = await sharedPlaylist(e, "1.1.1.2");
  await post(e, `/api/playlists/${a}/like`, {}, "7.7.7.7");
  const top = await (await get(e, "/api/playlists?cadence=170")).json();
  assert.deepEqual(top.playlists.map((p) => p.slug), [a, b]);
  assert.equal(top.playlists[0].likes, 1);
  const fresh = await (await get(e, "/api/playlists?cadence=170&sort=new")).json();
  assert.deepEqual(fresh.playlists.map((p) => p.slug), [b, a]);
});
