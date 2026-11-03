// Running-pun playlist names: pun x edition x number, ~80,000 combinations per
// language. Deterministic from a seed, so the same playlist keeps its name.

export function fnv1a(text) {
  let h = 0x811c9dc5;
  for (const ch of String(text)) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** Seed from the playlist's own content, so the server can recompute the name. */
export function playlistSeed(keys, cadence) {
  return [...keys].sort().join("\n") + `\n${cadence}`;
}

// MurmurHash3 finaliser: FNV-1a alone leaves the low bits poorly mixed for
// near-identical seeds, which clusters names.
function fmix32(h) {
  h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b) >>> 0;
  h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35) >>> 0;
  h ^= h >>> 16;
  return h >>> 0;
}

export function playlistName(seed, lang, names) {
  const set = names[lang] || names.en;
  let x = fmix32(fnv1a(seed));
  const pun = set.puns[x % set.puns.length];
  x = Math.floor(x / set.puns.length);
  const edition = set.editions[x % set.editions.length];
  x = Math.floor(x / set.editions.length);
  const n = 1 + (x % 99);
  return set.format.replace("{pun}", pun).replace("{edition}", edition).replace("{n}", String(n));
}
