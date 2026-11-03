"""Cadence (SPM) to BPM matching.

Two ways a track can fit a target cadence:

* **direct** - the track's BPM is the step frequency, one step per beat.
* **half-time** - the BPM is half the step frequency, two steps per beat.
  A 170 SPM run also works to an 85 BPM track.

Both are checked by default. When a track qualifies either way, the closer
reading wins and the chosen one is reported in the ``match`` column.
"""
from __future__ import annotations

import pandas as pd


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
