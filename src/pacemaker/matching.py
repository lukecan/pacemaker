"""Cadence (SPM) to BPM matching.

Two ways a track can fit a target cadence:

* **direct** - the track's BPM is the step frequency, one step per beat.
* **half-time** - the BPM is half the step frequency, two steps per beat.
  A 170 SPM run also works to an 85 BPM track.

Both are checked by default. When a track qualifies either way, the closer
reading wins and the chosen one is reported in the ``match`` column.
"""
from __future__ import annotations

import re

import pandas as pd

#: Playlists the streaming service builds from what you actually play or
#: love. Being in one means the listener knows the song well, whatever else
#: the export says.
HEAVY_ROTATION = re.compile(
    r"^\s*(replay\s*(\d{4}|all\s*time)?|favou?rite\s*songs|heavy\s*rotation.*"
    r"|on\s*repeat|repeat\s*rewind|your\s*top\s*songs.*|liked\s*songs|loved\s*tracks)\s*$",
    re.IGNORECASE,
)


def in_heavy_rotation(playlists) -> bool:
    if not isinstance(playlists, str) or not playlists:
        return False
    return any(HEAVY_ROTATION.match(name) for name in playlists.split(";"))


def match_tracks(df: pd.DataFrame, target: float, tolerance: float = 2.0,
                 direct: bool = True, half_time: bool = True) -> pd.DataFrame:
    """Return the tracks that fit ``target`` SPM.

    Adds ``match`` (``direct`` or ``half-time``) and ``diff``, the distance
    from the target in SPM, which is what ``select`` ranks on.
    """
    if not direct and not half_time:
        raise ValueError("enable at least one of direct or half-time matching")

    candidates = df[df["bpm"].notna()].copy()
    diff_direct = (candidates["bpm"] - target).abs()
    diff_half = (candidates["bpm"] * 2 - target).abs()

    fits_direct = (diff_direct <= tolerance) if direct else pd.Series(False, index=candidates.index)
    fits_half = (diff_half <= tolerance) if half_time else pd.Series(False, index=candidates.index)
    candidates = candidates[fits_direct | fits_half]
    if candidates.empty:
        return candidates.assign(match=pd.Series(dtype=str), diff=pd.Series(dtype=float))

    diff_direct = diff_direct.loc[candidates.index]
    diff_half = diff_half.loc[candidates.index]
    use_direct = fits_direct.loc[candidates.index] & (
        ~fits_half.loc[candidates.index] | (diff_direct <= diff_half))

    candidates["match"] = use_direct.map({True: "direct", False: "half-time"})
    candidates["diff"] = diff_direct.where(use_direct, diff_half)
    return candidates


def select(matched: pd.DataFrame, count: int = 30, seed: int = 0,
           prefer_favorites: bool = False) -> pd.DataFrame:
    """Pick ``count`` distinct tracks, closest to the target first.

    The frame is shuffled before a stable sort, so repeated runs with
    different seeds give different playlists at the same closeness.
    """
    if matched.empty:
        return matched
    shuffled = matched.sample(frac=1, random_state=seed)
    sort_keys = ["diff"]
    if prefer_favorites:
        shuffled["_not_favorite"] = ~shuffled["favorite"].astype(bool)
        sort_keys = ["_not_favorite", "diff"]
    ordered = shuffled.sort_values(sort_keys, kind="stable").drop_duplicates("key")
    return ordered.head(count).drop(columns=[c for c in ["_not_favorite"] if c in ordered])


DEFAULT_TRACK_SECONDS = 210
"""Assumed length when neither the export nor a provider knew one."""


def familiarity(df: pd.DataFrame) -> pd.Series:
    """How well the listener already knows each track, from 0 (fresh) to 1.

    Built from whatever signals the catalog has, each scaled to 0..1 and then
    averaged, so a library export with fewer signals still works:

    * served   - how often pacemaker has already put it in a playlist
    * favorite - marked as a favorite / liked / loved
    * playlists - how many of the listener's own playlists hold it
    * plays    - play count, when the export carries one
    * age      - how long ago it was added, when the export carries a date

    Signals that no track in the catalog has are left out of the average,
    rather than counted as zero for everyone.

    A track in a play-history playlist (Apple's "Replay 2026" or "Favourite
    Songs", Spotify's "On Repeat" or "Your Top Songs") is treated as well
    known regardless of the other signals.
    """
    if df.empty:
        return pd.Series(dtype=float)
    parts: list[pd.Series] = []

    def ranked(values: pd.Series) -> pd.Series:
        # percentile rank, robust to wildly different scales between users
        return values.rank(pct=True, method="average")

    served = df.get("served_count")
    if served is not None and served.fillna(0).gt(0).any():
        parts.append(ranked(served.fillna(0)))

    if "favorite" in df and df["favorite"].astype(bool).nunique() > 1:
        parts.append(df["favorite"].astype(bool).astype(float))

    if "playlists" in df:
        counts = df["playlists"].fillna("").map(
            lambda s: 0 if not s else len([p for p in str(s).split(";")
                                           if p.strip() and not HEAVY_ROTATION.match(p)]))
        if counts.gt(0).any() and counts.nunique() > 1:
            parts.append(counts.clip(upper=3) / 3.0)

    plays = df.get("play_count")
    if plays is not None and plays.notna().any() and plays.fillna(0).nunique() > 1:
        parts.append(ranked(plays.fillna(0)))

    added = df.get("added_at")
    if added is not None and added.notna().any():
        dates = pd.to_datetime(added, errors="coerce")
        if dates.notna().sum() > 1:
            # added longer ago -> more familiar; unknown dates sit in the middle
            parts.append(ranked(-dates.astype("int64").where(dates.notna())).fillna(0.5))

    score = (pd.concat(parts, axis=1).mean(axis=1) if parts
             else pd.Series(0.5, index=df.index))
    if "playlists" in df:
        heavy = df["playlists"].map(in_heavy_rotation).astype(bool)
        score = score.where(~heavy, score.clip(lower=0.95))
    return score


def assemble(matched: pd.DataFrame, *, minutes: float | None = 60.0, count: int | None = None,
             fresh: float | None = None, seed: int = 0, prefer_favorites: bool = False,
             assume_seconds: int = DEFAULT_TRACK_SECONDS) -> pd.DataFrame:
    """Build the playlist from matched candidates.

    Length: stops at whichever track count brings the total closest to
    ``minutes`` of music, or, if ``minutes`` is falsy, at ``count`` tracks.

    Rotation is always on. Every candidate is already within tolerance, and a
    BPM or two is inaudible while running, so closeness to the target is not
    used for ordering. Instead, tracks served least often by earlier
    playlists come first, with ties broken by ``seed``. That keeps one week's
    playlist from repeating the last.

    ``fresh`` is the share of the playlist, 0..1, that should be new to the
    listener. When the catalog holds discovered songs (``origin ==
    'discovery'``), "new" means those: songs the listener does not have yet.
    If there are not enough at this cadence, the rest is filled from the
    library. Without any discoveries, "new" falls back to the less familiar
    half of the library (see ``familiarity``).

    With ``fresh`` unset or 0, discovered songs are left out entirely.
    """
    if matched.empty:
        return matched
    if "origin" in matched:
        if not fresh:
            matched = matched[matched["origin"].fillna("library") != "discovery"]
            if matched.empty:
                return matched
    ordered = matched.drop_duplicates("key").sample(frac=1, random_state=seed).copy()
    if "served_count" not in ordered:
        ordered["served_count"] = 0
    ordered["served_count"] = ordered["served_count"].fillna(0)
    sort_keys = ["served_count"]
    if prefer_favorites:
        ordered["_not_favorite"] = ~ordered["favorite"].astype(bool)
        sort_keys.append("_not_favorite")
    ordered = ordered.sort_values(sort_keys, kind="stable")
    ordered = ordered.drop(columns=[c for c in ["_not_favorite"] if c in ordered])
    ordered["familiarity"] = familiarity(ordered).reindex(ordered.index)

    discovered = (ordered["origin"].fillna("library") == "discovery"
                  if "origin" in ordered else pd.Series(False, index=ordered.index))
    if discovered.any():
        is_fresh = discovered
    else:
        # No discoveries: the less familiar half of the library counts as fresh.
        is_fresh = ordered["familiarity"] < ordered["familiarity"].median()
        if not is_fresh.any() or is_fresh.all():  # no spread: split by rank instead
            ranks = ordered["familiarity"].rank(method="first")
            is_fresh = ranks <= len(ordered) / 2
    ordered["fresh"] = is_fresh

    if fresh is None:
        queue_fresh, queue_known = [], list(ordered.index)
    else:
        queue_fresh = [i for i in ordered.index if is_fresh[i]]
        queue_known = [i for i in ordered.index if not is_fresh[i]]

    lengths = ordered.get("duration_sec")
    lengths = (lengths.fillna(assume_seconds) if lengths is not None
               else pd.Series(assume_seconds, index=ordered.index))
    target_seconds = minutes * 60 if minutes else None
    limit = None if minutes else (count or 30)

    picked: list = []
    fresh_taken = 0
    total = 0.0
    while queue_fresh or queue_known:
        if fresh is None:
            take_fresh = False
        elif not queue_known:
            take_fresh = True
        elif not queue_fresh:
            take_fresh = False
        else:
            # keep the running share of fresh tracks as close to the target as
            # whole tracks allow
            take_fresh = fresh_taken + 0.5 <= fresh * (len(picked) + 1)
        index = (queue_fresh if take_fresh else queue_known).pop(0)
        before = total
        picked.append(index)
        fresh_taken += take_fresh
        total += float(lengths[index])
        if target_seconds is not None and total >= target_seconds:
            # stop at whichever side of the target is closer
            if len(picked) > 1 and (total - target_seconds) > (target_seconds - before):
                picked.pop()
            break
        if limit is not None and len(picked) >= limit:
            break

    return ordered.loc[picked].copy()


def select_minutes(matched: pd.DataFrame, minutes: float = 60.0, seed: int = 0,
                   prefer_favorites: bool = False,
                   assume_seconds: int = DEFAULT_TRACK_SECONDS) -> pd.DataFrame:
    """Fill to ``minutes`` of music, closest to the target first."""
    return assemble(matched, minutes=minutes, seed=seed,
                    prefer_favorites=prefer_favorites, assume_seconds=assume_seconds)


def playlist_seconds(playlist: pd.DataFrame,
                     assume_seconds: int = DEFAULT_TRACK_SECONDS) -> tuple[int, int]:
    """Total seconds and how many of those tracks had no known length."""
    if playlist.empty or "duration_sec" not in playlist:
        return len(playlist) * assume_seconds, len(playlist)
    known = playlist["duration_sec"].dropna()
    unknown = len(playlist) - len(known)
    return int(known.sum() + unknown * assume_seconds), unknown
