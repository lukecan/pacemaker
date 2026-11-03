import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import { parseCSV, toCSV } from "../site/lib/csv.js";
import { rankSimilar, seedArtists } from "../site/lib/discovery.js";
import { loadCSV, parseDuration } from "../site/lib/library.js";
import { assemble, familiarity, matchTracks, playlistSeconds } from "../site/lib/match.js";
import { playlistName, playlistSeed } from "../site/lib/names.js";
import { makeKey, normalizeArtist, normalizeTitle, pickBest, seedName, similarity } from "../site/lib/text.js";

const names = JSON.parse(fs.readFileSync(new URL("../site/lib/names.json", import.meta.url), "utf8"));

test("text normalisation matches the Python package", () => {
  assert.equal(normalizeTitle("A-Six (Original Long Version)"), "A-Six");
  assert.equal(normalizeTitle("Zero - Remastered 2012"), "Zero");
  assert.equal(normalizeTitle("Song (feat. X)"), "Song");
  assert.equal(normalizeTitle("(Only Parens)"), "(Only Parens)");
  assert.equal(normalizeArtist("Mumford & Sons"), "Mumford");
  assert.equal(seedName("Mumford & Sons"), "Mumford & Sons");
  assert.equal(seedName("Gorillaz feat. De La Soul"), "Gorillaz");
  assert.equal(makeKey("Rehab", "Amy Winehouse", " gbum70603730 "), "GBUM70603730");
  assert.equal(makeKey("Rehab", "Amy Winehouse", ""), "rehab|amy winehouse");
});

test("similarity equals difflib's ratio on known pairs", () => {
  // values computed with Python's difflib.SequenceMatcher(None, a, b).ratio()
  assert.equal(similarity("Rehab", "Rehab"), 1);
  assert.ok(Math.abs(similarity("Aerodynamic", "Aerodynamite") - 0.8695652173913043) < 1e-12);
  assert.ok(Math.abs(similarity("The Strokes", "The Rolling Stones") - 0.6206896551724138) < 1e-12);
  assert.equal(pickBest([{ title: "Totally Other", artist: "Nobody", bpm: 170 }], "Rehab", "Amy Winehouse"), null);
});

test("CSV parser handles quotes, commas, newlines and BOM", () => {
  const rows = parseCSV('﻿a,b\r\n"x, y","he said ""hi"""\r\n"multi\nline",2\n');
  assert.deepEqual(rows, [{ a: "x, y", b: 'he said "hi"' }, { a: "multi\nline", b: "2" }]);
  assert.equal(parseCSV(toCSV(rows, ["a", "b"])).length, 2);
});

test("TuneMyMusic export: songs only, favorites and playlists merged by ISRC", () => {
  const csv = [
    "Track name,Artist name,Album,Playlist name,Type,ISRC,Apple - id",
    "Rehab,Amy Winehouse,Back to Black,Library Songs,Favorite,GBUM70603730,1",
    "Rehab,Amy Winehouse,Back to Black (Deluxe),Replay 2026,Playlist,GBUM70603730,2",
    "Toto IV,Toto,Toto IV,Library Albums,Album,,3",
    "Toto,,,Library Artists,Artist,,4",
  ].join("\n");
  const songs = loadCSV(csv);
  assert.equal(songs.length, 1);
  assert.equal(songs[0].favorite, true);
  assert.deepEqual(songs[0].playlists, ["Replay 2026"]);
});

test("Spotify export: durations in ms and added dates", () => {
  const songs = loadCSV("Track URI,Track Name,Artist Name(s),Duration (ms),ISRC,Added At\n" +
    "spotify:track:1,Rehab,Amy Winehouse,214000,GBUM70603730,2019-04-01T10:00:00Z\n");
  assert.equal(songs[0].duration, 214);
  assert.equal(songs[0].addedAt, "2019-04-01");
  assert.equal(parseDuration("3:35"), 215);
  assert.equal(parseDuration("5"), null);
});

test("a file without title or artist columns is rejected", () => {
  assert.throws(() => loadCSV("foo,bar\n1,2\n"), /no-columns/);
});

const song = (i, extra = {}) => ({ key: `k${i}`, title: `t${i}`, artist: "x", bpm: 170, duration: 240,
  favorite: false, playlists: [], ...extra });

test("direct and half-time matching", () => {
  const m = matchTracks([168, 172, 173, 85, 86, 87, 90].map((bpm, i) => song(i, { bpm })), 170, 2);
  assert.deepEqual(m.map((t) => t.bpm).sort((a, b) => a - b), [85, 86, 168, 172]);
  assert.deepEqual([...new Set(m.filter((t) => t.bpm < 100).map((t) => t.match))], ["half-time"]);
});

test("one hour lands on the closer side of 60 minutes", () => {
  const pool = (secs) => matchTracks(Array.from({ length: 40 }, (_, i) => song(i, { duration: secs })), 170, 3);
  assert.equal(assemble(pool(240), { minutes: 60 }).length, 15);
  assert.equal(assemble(pool(250), { minutes: 60 }).length, 14);
  assert.equal(assemble(pool(262), { minutes: 60 }).length, 14);
  assert.deepEqual(playlistSeconds(assemble(pool(240), { minutes: 60 })), { seconds: 3600, unknown: 0 });
});

test("rotation keeps consecutive playlists apart", () => {
  const pool = matchTracks(Array.from({ length: 60 }, (_, i) => song(i)), 170, 3);
  const first = assemble(pool, { minutes: null, count: 15, seed: 1 });
  const served = Object.fromEntries(first.map((t) => [t.key, 1]));
  const second = assemble(pool, { minutes: null, count: 15, seed: 2, served });
  assert.equal(first.filter((t) => second.some((u) => u.key === t.key)).length, 0);
});

test("fresh share comes from discovered songs, and only when asked", () => {
  const lib = Array.from({ length: 30 }, (_, i) => song(i));
  const found = Array.from({ length: 10 }, (_, i) => song(100 + i, { origin: "discovery" }));
  const pool = matchTracks([...lib, ...found], 170, 3);
  const p = assemble(pool, { minutes: null, count: 10, fresh: 0.3 });
  assert.equal(p.filter((t) => t.origin === "discovery").length, 3);
  assert.equal(assemble(pool, { minutes: null, count: 10, fresh: 0 }).some((t) => t.origin === "discovery"), false);
});

test("Replay and Favourite Songs always count as well known", () => {
  const songs = [song(0, { playlists: ["Replay 2026"] }), song(1, { playlists: ["Soul 45"] }),
    song(2, { favorite: true }), song(3, { favorite: true, playlists: ["Soul 45"] })];
  const f = familiarity(songs);
  assert.ok(f[0] >= 0.95);
  assert.ok(f[1] < 0.5);
});

test("seed ranking: Replay counts five times, new artists outrank your own", () => {
  const seeds = seedArtists([song(0, { artist: "The Strokes", playlists: ["Replay 2026"] }),
    song(1, { artist: "Pearl Jam" })]);
  assert.deepEqual(seeds[0], { name: "The Strokes", weight: 5 });
  const order = rankSimilar(
    [{ name: "The Strokes", weight: 9 }, { name: "Pearl Jam", weight: 4 }],
    { "The Strokes": [{ id: 1, name: "Pearl Jam" }, { id: 2, name: "Interpol" }, { id: 3, name: "Cage The Elephant" }],
      "Pearl Jam": [{ id: 3, name: "Cage The Elephant" }, { id: 4, name: "Soundgarden" }] },
    new Set(["the strokes", "pearl jam", "soundgarden"]),
  ).map((a) => a.name);
  assert.equal(order[0], "Cage The Elephant");
  assert.ok(order.indexOf("Interpol") < order.indexOf("Soundgarden"));
  assert.equal(order.at(-1), "Pearl Jam");
});

test("playlist names are stable, per language, and spread evenly", () => {
  const seed = playlistSeed(["b", "a"], 170);
  assert.equal(seed, playlistSeed(["a", "b"], 170));
  assert.equal(playlistName(seed, "en", names), playlistName(seed, "en", names));
  assert.match(playlistName(seed, "tr", names), / · .+ No\. \d{1,2}$/);
  const distinct = new Set(Array.from({ length: 5000 }, (_, i) => playlistName(`s${i}`, "en", names)));
  assert.ok(distinct.size > 4800);
});
