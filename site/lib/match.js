// Matching, familiarity, rotation and playlist assembly.
// A faithful port of pacemaker/matching.py.

export const DEFAULT_TRACK_SECONDS = 210;

export const HEAVY_ROTATION = /^\s*(replay\s*(\d{4}|all\s*time)?|favou?rite\s*songs|heavy\s*rotation.*|on\s*repeat|repeat\s*rewind|your\s*top\s*songs.*|liked\s*songs|loved\s*tracks)\s*$/i;

export function inHeavyRotation(playlists) {
  return (playlists || []).some((p) => HEAVY_ROTATION.test(p));
}

/** Tracks that fit the cadence directly (1 step per beat) or at half-time (2 steps). */
export function matchTracks(songs, target, tolerance = 3, { direct = true, halfTime = true } = {}) {
  const out = [];
  for (const s of songs) {
    if (!s.bpm) continue;
    const dDirect = Math.abs(s.bpm - target);
    const dHalf = Math.abs(s.bpm * 2 - target);
    const okDirect = direct && dDirect <= tolerance;
    const okHalf = halfTime && dHalf <= tolerance;
    if (!okDirect && !okHalf) continue;
    const useDirect = okDirect && (!okHalf || dDirect <= dHalf);
    out.push({ ...s, match: useDirect ? "direct" : "half-time", diff: useDirect ? dDirect : dHalf });
  }
  return out;
}

function rankPct(values) {
  const idx = values.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
  const ranks = new Array(values.length);
  for (let i = 0; i < idx.length; ) {
    let j = i;
    while (j + 1 < idx.length && idx[j + 1][0] === idx[i][0]) j++;
    const avg = (i + j + 2) / 2;
    for (let k = i; k <= j; k++) ranks[idx[k][1]] = avg / values.length;
    i = j + 1;
  }
  return ranks;
}

/** 0 (fresh) .. 1 (well known), averaged over whichever signals the library carries. */
export function familiarity(songs, served = {}) {
  const n = songs.length;
  if (!n) return [];
  const parts = [];
  const servedVals = songs.map((s) => served[s.key] || 0);
  if (servedVals.some((v) => v > 0)) parts.push(rankPct(servedVals));
  const fav = songs.map((s) => (s.favorite ? 1 : 0));
  if (new Set(fav).size > 1) parts.push(fav);
  const counts = songs.map((s) => (s.playlists || []).filter((p) => p.trim() && !HEAVY_ROTATION.test(p)).length);
  if (counts.some((c) => c > 0) && new Set(counts).size > 1) parts.push(counts.map((c) => Math.min(c, 3) / 3));
  const plays = songs.map((s) => s.playCount);
  if (plays.some((p) => p != null) && new Set(plays.map((p) => p ?? 0)).size > 1) parts.push(rankPct(plays.map((p) => p ?? 0)));
  const dates = songs.map((s) => (s.addedAt ? Date.parse(s.addedAt) : null));
  if (dates.filter((d) => d != null).length > 1) {
    const known = dates.map((d, i) => [d, i]).filter(([d]) => d != null);
    const r = rankPct(known.map(([d]) => -d));
    const age = new Array(n).fill(0.5);
    known.forEach(([, i], k) => { age[i] = r[k]; });
    parts.push(age);
  }
  return songs.map((s, i) => {
    const score = parts.length ? parts.reduce((acc, p) => acc + p[i], 0) / parts.length : 0.5;
    return inHeavyRotation(s.playlists) ? Math.max(score, 0.95) : score;
  });
}

export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function shuffle(items, seed) {
  const rand = mulberry32(seed);
  const a = items.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/**
 * Build the playlist.
 *  - length: the track count that brings the total closest to `minutes`
 *  - rotation: least-served first (all candidates are within tolerance anyway)
 *  - fresh: share 0..1 taken from discovered songs (origin "discovery");
 *    without any, from the less familiar half of the library
 */
export function assemble(candidates, { minutes = 60, count = null, fresh = null, seed = 0,
  served = {}, assumeSeconds = DEFAULT_TRACK_SECONDS } = {}) {
  let pool = candidates;
  if (!fresh) pool = pool.filter((c) => c.origin !== "discovery");
  const seen = new Set();
  pool = pool.filter((c) => (seen.has(c.key) ? false : seen.add(c.key)));
  if (!pool.length) return [];

  const ordered = shuffle(pool, seed)
    .map((c, i) => ({ c, i, s: served[c.key] || 0 }))
    .sort((a, b) => a.s - b.s || a.i - b.i)
    .map(({ c }) => c);

  const fam = familiarity(ordered, served);
  const discovered = ordered.map((c) => c.origin === "discovery");
  let isFresh;
  if (discovered.some(Boolean)) {
    isFresh = discovered;
  } else {
    const sorted = [...fam].sort((a, b) => a - b);
    const mid = sorted.length % 2 ? sorted[(sorted.length - 1) / 2]
      : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
    isFresh = fam.map((f) => f < mid);
    if (!isFresh.some(Boolean) || isFresh.every(Boolean)) {
      const order = fam.map((f, i) => [f, i]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      isFresh = new Array(fam.length).fill(false);
      order.slice(0, Math.floor(fam.length / 2)).forEach(([, i]) => { isFresh[i] = true; });
    }
  }
  const items = ordered.map((c, i) => ({ ...c, familiarity: fam[i], fresh: isFresh[i] }));
  const qFresh = fresh ? items.filter((x) => x.fresh) : [];
  const qKnown = fresh ? items.filter((x) => !x.fresh) : items.slice();

  const target = minutes ? minutes * 60 : null;
  const limit = minutes ? null : (count || 30);
  const picked = [];
  let freshTaken = 0;
  let total = 0;
  while (qFresh.length || qKnown.length) {
    let takeFresh;
    if (!fresh) takeFresh = false;
    else if (!qKnown.length) takeFresh = true;
    else if (!qFresh.length) takeFresh = false;
    else takeFresh = freshTaken + 0.5 <= fresh * (picked.length + 1);
    const item = (takeFresh ? qFresh : qKnown).shift();
    const before = total;
    picked.push(item);
    if (takeFresh) freshTaken++;
    total += item.duration || assumeSeconds;
    if (target != null && total >= target) {
      if (picked.length > 1 && total - target > target - before) picked.pop();
      break;
    }
    if (limit != null && picked.length >= limit) break;
  }
  return picked;
}

export function playlistSeconds(playlist, assumeSeconds = DEFAULT_TRACK_SECONDS) {
  let seconds = 0;
  let unknown = 0;
  for (const t of playlist) {
    if (t.duration) seconds += t.duration;
    else { seconds += assumeSeconds; unknown++; }
  }
  return { seconds, unknown };
}
