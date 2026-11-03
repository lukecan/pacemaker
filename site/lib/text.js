// Text normalisation shared by the browser and the Worker.
// Ported from the Python package so both sides build identical keys.

const PAREN = /\s*[([][^)\]]*[)\]]/g;
const DASH_SUFFIX = /\s+-\s+(remaster(ed)?|live|single|radio|album|mono|stereo|demo|version|mix)\b.*$/i;
const FEAT = /\s+(feat\.?|ft\.?|featuring|with)\s+.*$/i;
const ARTIST_SPLIT = /\s*[,&/]\s*|\s+(?:x|vs\.?)\s+/;
const SEED_SPLIT = /\s+(?:feat\.?|ft\.?|featuring)\s+|\s*,\s*/i;

const squash = (s) => String(s ?? "").split(/\s+/).filter(Boolean).join(" ");

/** Strip edition noise ("(Remastered)", "- Live", "feat. X") that breaks lookups. */
export function normalizeTitle(title) {
  const original = squash(title);
  let t = String(title ?? "").replace(PAREN, "");
  t = t.replace(DASH_SUFFIX, "").replace(FEAT, "");
  return squash(t) || original;
}

/** First artist only, for track searches ("A & B" -> "A"). */
export function normalizeArtist(artist) {
  const original = squash(artist);
  const a = String(artist ?? "").replace(FEAT, "");
  return squash(a.split(ARTIST_SPLIT)[0]) || original;
}

/** Artist name for an artist search: drop guests, keep "Mumford & Sons" whole. */
export function seedName(artist) {
  return squash(String(artist ?? "").split(SEED_SPLIT)[0]);
}

export const norm = (s) => squash(s).toLowerCase();

/** ISRC identifies the exact recording; fall back to title + artist. */
export function makeKey(title, artist, isrc) {
  if (typeof isrc === "string" && isrc.trim()) return isrc.trim().toUpperCase();
  return `${String(title ?? "").trim().toLowerCase()}|${String(artist ?? "").trim().toLowerCase()}`;
}

// Ratcliff/Obershelp similarity, the same measure as Python's difflib ratio.
function longestMatch(a, b, alo, ahi, blo, bhi) {
  let best = [alo, blo, 0];
  let prev = new Map();
  for (let i = alo; i < ahi; i++) {
    const cur = new Map();
    for (let j = blo; j < bhi; j++) {
      if (a[i] === b[j]) {
        const k = (prev.get(j - 1) || 0) + 1;
        cur.set(j, k);
        if (k > best[2]) best = [i - k + 1, j - k + 1, k];
      }
    }
    prev = cur;
  }
  return best;
}

function matchingChars(a, b, alo, ahi, blo, bhi) {
  const [i, j, k] = longestMatch(a, b, alo, ahi, blo, bhi);
  if (!k) return 0;
  return k + matchingChars(a, b, alo, i, blo, j) + matchingChars(a, b, i + k, ahi, j + k, bhi);
}

export function similarity(a, b) {
  const x = String(a ?? "").toLowerCase();
  const y = String(b ?? "").toLowerCase();
  if (!x.length && !y.length) return 1;
  return (2 * matchingChars(x, y, 0, x.length, 0, y.length)) / (x.length + y.length);
}

/** The candidate that really is this song, or null. Each candidate: {title, artist, bpm}. */
export function pickBest(candidates, title, artist, threshold = 0.72) {
  let best = null;
  let bestScore = 0;
  for (const c of candidates) {
    if (!c || !c.bpm) continue;
    const score = 0.65 * similarity(c.title, title) + 0.35 * similarity(c.artist, artist);
    if (score > bestScore) { best = c; bestScore = score; }
  }
  return best && bestScore >= threshold ? best : null;
}

export function plausibleBpm(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 30 && n < 300 ? n : null;
}
