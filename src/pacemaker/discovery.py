"""Find songs the listener does not have yet, by artists like the ones they play.

1. **Seeds.** Artists ranked by how much of the library they fill, with songs
   in play-history playlists (Replay, Favourite Songs, On Repeat ...) counted
   five times, since those show what is actually being listened to now.
2. **Similar artists.** Deezer's related artists for each seed. An artist
   suggested by several seeds ranks higher; one the listener has no songs by
   at all gets a bonus, since that is the most "new", and one that is itself
   among the listener's main artists is pushed well down (not removed: a
   song you do not have by an artist you love is still a find).
3. **Songs.** The top tracks of the best-ranked similar artists, minus
   anything already in the library, each opened once to read its BPM.

Everything goes into the catalog with ``origin = 'discovery'``. Songs Deezer
has no BPM for are remembered too, so a later run does not fetch them again.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict

import pandas as pd

from . import catalog, providers
from .library import make_key
from .matching import in_heavy_rotation
from .providers import normalize_artist, normalize_title


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def seed_name(artist: str) -> str:
    """Artist name for an artist search: drop featured guests, keep the band.

    ``normalize_artist`` also splits on "&" so that duets match a single
    artist in track searches, but that turns "Mumford & Sons" into
    "Mumford", which Deezer's artist search cannot find.
    """
    import re
    name = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+|\s*,\s*", str(artist or ""),
                    maxsplit=1, flags=re.IGNORECASE)[0]
    return " ".join(name.split())


def seed_artists(library: pd.DataFrame, count: int = 30) -> list[tuple[str, float]]:
    """The listener's artists, heaviest first, as ``(name, weight)``."""
    lib = library[library.get("origin", pd.Series("library", index=library.index))
                  .fillna("library") == "library"]
    weights: dict[str, float] = defaultdict(float)
    display: dict[str, str] = {}
    for row in lib.itertuples(index=False):
        name = seed_name(row.artist)
        if not name:
            continue
        key = _norm(name)
        display.setdefault(key, name)
        weights[key] += 5.0 if in_heavy_rotation(getattr(row, "playlists", "")) else 1.0
    ranked = sorted(weights.items(), key=lambda kv: -kv[1])[:count]
    return [(display[k], w) for k, w in ranked]


NEW_ARTIST_BONUS = 1.5
OWN_SEED_PENALTY = 0.3


def rank_similar(seed_list: list[tuple[str, float]],
                 related_by_seed: dict[str, list[dict]],
                 owned_artists: set[str]) -> list[tuple[int, str, float, list[str]]]:
    """Score related artists across all seeds, best first.

    Returns ``(artist_id, name, score, suggested_by)``.
    """
    seed_names = {_norm(name) for name, _ in seed_list}
    scores: dict[int, float] = defaultdict(float)
    names: dict[int, str] = {}
    suggested_by: dict[int, list[str]] = defaultdict(list)
    for name, weight in seed_list:
        for rank, rel in enumerate(related_by_seed.get(name, [])):
            rel_name = rel.get("name", "")
            score = math.sqrt(weight) / (1 + rank * 0.15)
            if _norm(rel_name) in seed_names:
                score *= OWN_SEED_PENALTY
            elif _norm(rel_name) not in owned_artists:
                score *= NEW_ARTIST_BONUS
            scores[rel["id"]] += score
            names[rel["id"]] = rel_name
            suggested_by[rel["id"]].append(name)
    ordered = sorted(scores, key=lambda i: -scores[i])
    return [(i, names[i], scores[i], suggested_by[i]) for i in ordered]


def discover(con, seeds: int = 30, artists: int = 80, per_artist: int = 10,
             delay: float = 0.25, log=print) -> dict:
    """Run one discovery pass and store what it finds. Returns counts."""
    library = catalog.all_tracks(con)
    own = library[library["origin"].fillna("library") == "library"]
    owned_keys = set(own["key"])
    owned_titles = {(_norm(normalize_title(t)), _norm(normalize_artist(a)))
                    for t, a in zip(own["title"], own["artist"])}
    owned_artists = {_norm(normalize_artist(a)) for a in own["artist"]}
    seen_ids = catalog.known_deezer_ids(con)

    seed_list = seed_artists(library, seeds)
    log(f"seeds: {', '.join(name for name, _ in seed_list[:12])}"
        f"{' ...' if len(seed_list) > 12 else ''}")

    def pause():
        if delay:
            time.sleep(delay)

    # -- similar artists, scored across all seeds
    related_by_seed: dict[str, list[dict]] = {}
    for name, _ in seed_list:
        try:
            artist = providers.deezer_find_artist(name)
            pause()
            if not artist:
                log(f"  no Deezer artist for {name!r}")
                continue
            related_by_seed[name] = providers.deezer_related(artist["id"])
            pause()
        except Exception as exc:  # noqa: BLE001 - keep going on a bad seed
            log(f"  skipped {name!r}: {type(exc).__name__}")

    ranked = rank_similar(seed_list, related_by_seed, owned_artists)[:artists]
    names = {i: n for i, n, _, _ in ranked}
    suggested_by = {i: by for i, _, _, by in ranked}
    chosen = [i for i, _, _, _ in ranked]
    log(f"similar artists: using {len(chosen)}: "
        f"{', '.join(names[i] for i in chosen[:12])}{' ...' if len(chosen) > 12 else ''}")

    # -- their top songs, minus what the listener has
    stats = {"seeds": len(seed_list), "artists": len(chosen), "checked": 0,
             "added": 0, "with_bpm": 0, "owned": 0}
    for artist_id in chosen:
        try:
            tracks = providers.deezer_top_tracks(artist_id, per_artist)
            pause()
        except Exception as exc:  # noqa: BLE001
            log(f"  top tracks failed for {names[artist_id]!r}: {type(exc).__name__}")
            continue
        for track in tracks:
            tid = str(track.get("id") or "")
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            ident = (_norm(normalize_title(track["title"])), _norm(normalize_artist(track["artist"])))
            if ident in owned_titles:
                stats["owned"] += 1
                continue
            try:
                full = providers.deezer_track(tid)
                pause()
            except Exception as exc:  # noqa: BLE001
                log(f"  track {tid} failed: {type(exc).__name__}")
                continue
            stats["checked"] += 1
            if not full:
                continue
            key = make_key(full["title"], full["artist"], full.get("isrc"))
            if key in owned_keys:
                stats["owned"] += 1
                continue
            row = {
                "key": key, "title": full["title"], "artist": full["artist"],
                "album": full.get("album") or track.get("album") or "",
                "isrc": full.get("isrc"), "deezer_id": tid,
                "duration_sec": full.get("duration") or track.get("duration"),
                "bpm": full.get("bpm"),
                "seed": "; ".join(suggested_by[artist_id][:3]),
            }
            if catalog.add_discovered(con, row):
                stats["added"] += 1
                stats["with_bpm"] += row["bpm"] is not None
        con.commit()

    log(f"discovery: checked {stats['checked']} songs, added {stats['added']} "
        f"({stats['with_bpm']} with BPM), skipped {stats['owned']} you already have")
    return stats
