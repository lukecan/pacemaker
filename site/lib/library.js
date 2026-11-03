// Load a library export into a list of unique songs.
// Same formats and column aliases as the Python package.

import { parseCSV } from "./csv.js";
import { makeKey, plausibleBpm } from "./text.js";

const SONG_TYPES = new Set(["favorite", "playlist", "song", "track", "liked"]);
const ALIASES = {
  title: ["track name", "name", "title", "track", "song", "song name", "track title"],
  artist: ["artist name", "artist name(s)", "artist", "artists", "album artist", "artist(s)", "album artist name(s)"],
  album: ["album", "album name", "album title"],
  isrc: ["isrc"],
  bpm: ["bpm", "tempo", "beats per minute"],
  duration: ["duration (ms)", "duration_ms", "track duration (ms)", "duration", "duration (s)", "length", "time", "track time"],
  playCount: ["play count", "plays", "playcount", "play_count"],
  addedAt: ["added at", "date added", "added", "added_at", "date_added"],
  sourceId: ["apple - id", "apple id", "track id", "track uri", "spotify id", "spotify uri", "id"],
  type: ["type"],
  playlist: ["playlist name", "playlist"],
};

export function parseDuration(value) {
  if (value == null) return null;
  const text = String(value).trim();
  if (!text) return null;
  let seconds;
  if (text.includes(":")) {
    const parts = text.split(":").map(Number);
    if (parts.some((p) => !Number.isFinite(p))) return null;
    seconds = parts.reduce((acc, n) => acc * 60 + n, 0);
  } else {
    seconds = Number(text);
    if (!Number.isFinite(seconds)) return null;
    if (seconds > 1800) seconds /= 1000; // milliseconds
  }
  seconds = Math.round(seconds);
  return seconds >= 10 && seconds <= 1800 ? seconds : null;
}

function parseCount(value) {
  const n = Math.trunc(Number(value));
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function parseDate(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

function resolveColumns(header) {
  const lowered = new Map(header.map((h) => [h.trim().toLowerCase(), h]));
  const found = {};
  for (const [canonical, aliases] of Object.entries(ALIASES)) {
    for (const alias of aliases) {
      if (lowered.has(alias)) { found[canonical] = lowered.get(alias); break; }
    }
  }
  return found;
}

function finalize(records) {
  const byKey = new Map();
  for (const r of records) {
    const title = String(r.title ?? "").trim();
    if (!title) continue;
    const artist = String(r.artist ?? "").trim();
    const key = makeKey(title, artist, r.isrc);
    const prev = byKey.get(key);
    if (!prev) {
      byKey.set(key, {
        key, title, artist, album: r.album || "", isrc: r.isrc || null,
        sourceId: r.sourceId || null, bpm: r.bpm ?? null, duration: r.duration ?? null,
        playCount: r.playCount ?? null, addedAt: r.addedAt ?? null,
        favorite: !!r.favorite, playlists: r.playlist ? [r.playlist] : [],
      });
    } else {
      prev.favorite ||= !!r.favorite;
      if (r.playlist && !prev.playlists.includes(r.playlist)) prev.playlists.push(r.playlist);
      prev.bpm = Math.max(prev.bpm ?? -1, r.bpm ?? -1) > 0 ? Math.max(prev.bpm ?? 0, r.bpm ?? 0) : null;
      prev.duration ??= r.duration ?? null;
      if (r.playCount != null) prev.playCount = Math.max(prev.playCount ?? 0, r.playCount);
      if (r.addedAt && (!prev.addedAt || r.addedAt < prev.addedAt)) prev.addedAt = r.addedAt;
    }
  }
  const songs = [...byKey.values()];
  for (const s of songs) s.playlists.sort();
  if (!songs.length) throw new Error("no-songs");
  return songs;
}

export function loadCSV(text) {
  const rows = parseCSV(text);
  if (!rows.length) throw new Error("no-songs");
  const cols = resolveColumns(Object.keys(rows[0]));
  if (!cols.title || !cols.artist) throw new Error("no-columns");
  const records = [];
  for (const row of rows) {
    const kind = cols.type ? String(row[cols.type] ?? "").trim().toLowerCase() : "";
    if (cols.type && !SONG_TYPES.has(kind)) continue;
    records.push({
      title: row[cols.title],
      artist: row[cols.artist],
      album: cols.album ? row[cols.album] : "",
      isrc: cols.isrc ? (row[cols.isrc] || "").trim() || null : null,
      sourceId: cols.sourceId ? row[cols.sourceId] || null : null,
      bpm: cols.bpm ? plausibleBpm(row[cols.bpm]) : null,
      duration: cols.duration ? parseDuration(row[cols.duration]) : null,
      playCount: cols.playCount ? parseCount(row[cols.playCount]) : null,
      addedAt: cols.addedAt ? parseDate(row[cols.addedAt]) : null,
      favorite: kind === "favorite" || kind === "liked",
      playlist: kind === "playlist" && cols.playlist ? row[cols.playlist] : null,
    });
  }
  return finalize(records);
}

// iTunes / Music.app Library.xml is an Apple plist. `parseXml` is DOMParser in
// the browser; tests pass their own.
function plistValue(node) {
  if (!node) return null;
  switch (node.tagName) {
    case "dict": {
      const out = {};
      const kids = [...node.children];
      for (let i = 0; i < kids.length; i += 2) out[kids[i].textContent] = plistValue(kids[i + 1]);
      return out;
    }
    case "array": return [...node.children].map(plistValue);
    case "integer": case "real": return Number(node.textContent);
    case "true": return true;
    case "false": return false;
    default: return node.textContent;
  }
}

export function loadXML(text, parseXml) {
  const doc = parseXml(text);
  const root = doc.getElementsByTagName("plist")[0]?.firstElementChild
    ?? doc.documentElement?.firstElementChild;
  const plist = plistValue(root) || {};
  const records = [];
  for (const t of Object.values(plist.Tracks || {})) {
    if (!t || !t.Name) continue;
    records.push({
      title: t.Name, artist: t.Artist || t["Album Artist"] || "", album: t.Album || "",
      isrc: null, sourceId: t["Track ID"] != null ? String(t["Track ID"]) : null,
      bpm: plausibleBpm(t.BPM), duration: parseDuration(t["Total Time"]),
      playCount: parseCount(t["Play Count"]) ?? 0, addedAt: parseDate(t["Date Added"]),
      favorite: !!t.Loved, playlist: null,
    });
  }
  return finalize(records);
}

export function loadLibrary(text, filename = "", parseXml = null) {
  const looksXml = /\.(xml|plist)$/i.test(filename) || /^\s*<\?xml|^\s*<plist/i.test(text);
  if (looksXml) {
    if (!parseXml) throw new Error("xml-unsupported");
    return loadXML(text, parseXml);
  }
  return loadCSV(text);
}
