import pandas as pd
import pytest

from pacemaker.matching import match_tracks, select


def catalog(bpms, favorites=None):
    return pd.DataFrame({
        "key": [f"k{i}" for i in range(len(bpms))],
        "title": [f"song {i}" for i in range(len(bpms))],
        "bpm": bpms,
        "favorite": favorites if favorites is not None else [True] * len(bpms),
    })


def test_direct_and_half_time_windows():
    matched = match_tracks(catalog([168, 172, 173, 85, 86, 87, 90]), 170, tolerance=2)
    assert set(matched["bpm"]) == {168, 172, 85, 86}
    assert set(matched[matched["bpm"] < 100]["match"]) == {"half-time"}
    assert set(matched[matched["bpm"] > 100]["match"]) == {"direct"}


def test_modes_can_be_turned_off():
    assert set(match_tracks(catalog([170, 85]), 170, 2, half_time=False)["bpm"]) == {170}
    assert set(match_tracks(catalog([170, 85]), 170, 2, direct=False)["bpm"]) == {85}
    with pytest.raises(ValueError):
        match_tracks(catalog([170]), 170, 2, direct=False, half_time=False)


def test_closer_reading_wins_when_both_fit():
    # 86 BPM is 172 half-time (2 off); nothing else fits, so half-time is used
    matched = match_tracks(catalog([86]), 170, tolerance=2)
    assert matched.iloc[0]["match"] == "half-time"
    assert matched.iloc[0]["diff"] == 2.0


def test_tracks_without_bpm_are_ignored():
    assert len(match_tracks(catalog([170, None, float("nan")]), 170, 2)) == 1


def test_no_candidates_returns_empty_frame_with_columns():
    matched = match_tracks(catalog([120]), 170, 2)
    assert matched.empty
    assert {"match", "diff"} <= set(matched.columns)


def test_selection_is_unique_and_closest_first():
    matched = match_tracks(catalog([170, 169, 172, 85]), 170, tolerance=2)
    chosen = select(matched, count=2)
    assert len(chosen) == 2
    assert chosen["key"].is_unique
    assert chosen.iloc[0]["diff"] == 0.0


def test_seed_changes_the_playlist():
    matched = match_tracks(catalog([169, 169, 169, 169, 171, 171, 171, 171]), 170, 2)
    first = list(select(matched, 3, seed=1)["key"])
    second = list(select(matched, 3, seed=9)["key"])
    assert first != second  # same closeness, different draw


def test_prefer_favorites_puts_favorites_first():
    matched = match_tracks(catalog([170, 170], favorites=[False, True]), 170, 2)
    assert select(matched, 1, prefer_favorites=True).iloc[0]["key"] == "k1"
