// Seed artists and similar-artist ranking. Port of pacemaker/discovery.py.

import { inHeavyRotation } from "./match.js";
import { norm, seedName } from "./text.js";

export const NEW_ARTIST_BONUS = 1.5;
export const OWN_SEED_PENALTY = 0.3;

/** The listener's artists, heaviest first, as [{name, weight}]. */
export function seedArtists(songs, count = 12) {
  const weights = new Map();
  const display = new Map();
  for (const s of songs) {
    if (s.origin === "discovery") continue;
    const name = seedName(s.artist);
    if (!name) continue;
    const key = norm(name);
    if (!display.has(key)) display.set(key, name);
    weights.set(key, (weights.get(key) || 0) + (inHeavyRotation(s.playlists) ? 5 : 1));
  }
  return [...weights.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, count)
    .map(([k, w]) => ({ name: display.get(k), weight: w }));
}

/** Related artists scored across seeds, best first: [{id, name, score, suggestedBy}]. */
export function rankSimilar(seeds, relatedBySeed, ownedArtists) {
  const seedNames = new Set(seeds.map((s) => norm(s.name)));
  const scores = new Map();
  for (const { name, weight } of seeds) {
    (relatedBySeed[name] || []).forEach((rel, rank) => {
      let score = Math.sqrt(weight) / (1 + rank * 0.15);
      const n = norm(rel.name);
      if (seedNames.has(n)) score *= OWN_SEED_PENALTY;
      else if (!ownedArtists.has(n)) score *= NEW_ARTIST_BONUS;
      const prev = scores.get(rel.id) || { id: rel.id, name: rel.name, score: 0, suggestedBy: [] };
      prev.score += score;
      prev.suggestedBy.push(name);
      scores.set(rel.id, prev);
    });
  }
  return [...scores.values()].sort((a, b) => b.score - a.score);
}
