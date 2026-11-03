import sqlite3

import pandas as pd
import pytest

from pacemaker import catalog
from pacemaker.library import _coerce_duration, load_library
from pacemaker.matching import assemble, familiarity, match_tracks, playlist_seconds


def candidates(n, seconds=200, **extra):
    df = pd.DataFrame({
        "key": [f"k{i}" for i in range(n)],
        "title": [f"song {i}" for i in range(n)],
        "bpm": [170.0] * n,
        "favorite": [False] * n,
        "duration_sec": [seconds] * n,
    })
    for col, values in extra.items():
        df[col] = values
    return match_tracks(df, 170, 2)


# ---------------------------------------------------------------- durations

@pytest.mark.parametrize("raw,expected", [
    ("215", 215), ("215000", 215), ("3:35", 215), ("1:02:00", None),
    ("", None), ("abc", None), (None, None), ("5", None),
])
def test_duration_formats(raw, expected):
    assert _coerce_duration(raw) == expected


def test_spotify_export_columns(tmp_path):
    path = tmp_path / "spotify.csv"
    path.write_text(
        "Track URI,Track Name,Artist Name(s),Album Name,Duration (ms),ISRC,Added At\n"
        "spotify:track:1,Rehab,Amy Winehouse,Back to Black,214000,GBUM70603730,2019-04-01T10:00:00Z\n",
        encoding="utf-8")
    row = load_library(str(path)).iloc[0]
    assert (row["title"], row["artist"], row["duration_sec"]) == ("Rehab", "Amy Winehouse", 214)
    assert row["added_at"] == "2019-04-01"
    assert row["source_id"] == "spotify:track:1"


# ------------------------------------------------------------- by duration

def test_one_hour_playlist_stops_once_the_hour_is_full():
    playlist = assemble(candidates(40, seconds=240), minutes=60)
    assert len(playlist) == 15  # 15 x 4 min = 60 min exactly
    assert playlist_seconds(playlist) == (3600, 0)


def test_length_lands_on_the_side_of_the_hour_that_is_closer():
    # 14 x 250 s = 58:20 (100 s short), 15 x 250 s = 62:30 (150 s over)
    assert len(assemble(candidates(40, seconds=250), minutes=60)) == 14
    # 15 x 236 s = 59:00 (60 s short), 16 x 236 s = 62:56 (176 s over)
    assert len(assemble(candidates(40, seconds=236), minutes=60)) == 15
    # 13 x 270 s = 58:30 (90 s short), 14 x 270 s = 63:00 (180 s over)
    assert len(assemble(candidates(40, seconds=270), minutes=60)) == 13
    # 14 x 262 s = 61:08 (68 s over) beats 13 x 262 s = 56:46 (194 s short)
    assert len(assemble(candidates(40, seconds=262), minutes=60)) == 14


def test_unknown_lengths_use_the_assumption_and_are_reported():
    df = candidates(40)
    df["duration_sec"] = None
    playlist = assemble(df, minutes=60, assume_seconds=180)
    assert len(playlist) == 20
    assert playlist_seconds(playlist, 180) == (3600, 20)


def test_count_mode_still_works():
    assert len(assemble(candidates(40), minutes=None, count=12)) == 12


# ---------------------------------------------------------------- freshness

def test_familiarity_uses_served_history():
    df = candidates(4, served_count=[0, 0, 5, 9])
    score = familiarity(df).tolist()
    assert score[0] == score[1] < score[2] < score[3]


def test_familiarity_ignores_signals_nobody_has():
    df = candidates(3)  # no favorites, no history, no plays
    assert familiarity(df).tolist() == [0.5, 0.5, 0.5]


def test_familiarity_combines_favorites_plays_and_age():
    df = candidates(
        3,
        favorite=[True, False, False],
        play_count=[50, 2, 0],
        added_at=["2015-01-01", "2024-06-01", "2026-10-01"],
    )
    score = familiarity(df).tolist()
    assert score[0] > score[1] > score[2]


@pytest.mark.parametrize("name", ["Replay 2026", "Replay All Time", "Favourite Songs",
                                  "Favorite Songs", "On Repeat", "Your Top Songs 2025"])
def test_play_history_playlists_mean_well_known(name):
    df = candidates(4, favorite=[False, False, True, True],
                    playlists=[name, "Soul 45", "", "Soul 45"])
    score = familiarity(df).tolist()
    assert score[0] >= 0.95       # only in Replay, still well known
    assert score[1] < 0.5         # only in an editorial playlist: fresh


def test_ordinary_playlist_named_like_replay_is_not_matched():
    from pacemaker.matching import in_heavy_rotation
    assert not in_heavy_rotation("Replay Value Mixtape")
    assert not in_heavy_rotation("Songs I replay at night")
    assert in_heavy_rotation("Soul 45; Replay 2024")


@pytest.mark.parametrize("share", [0.0, 0.3, 0.5, 1.0])
def test_fresh_share_is_respected(share):
    df = candidates(60, served_count=list(range(60)))
    playlist = assemble(df, minutes=None, count=20, fresh=share)
    assert len(playlist) == 20
    assert abs(playlist["fresh"].mean() - share) <= 0.05


def test_fresh_tracks_really_are_the_least_familiar():
    df = candidates(20, served_count=[0] * 10 + [8] * 10)
    playlist = assemble(df, minutes=None, count=5, fresh=1.0)
    assert set(playlist["served_count"]) == {0}


def test_running_out_of_fresh_tracks_fills_with_familiar_ones():
    df = candidates(6, served_count=[0, 0, 0, 9, 9, 9])
    playlist = assemble(df, minutes=None, count=6, fresh=1.0)
    assert len(playlist) == 6


def test_rotation_keeps_consecutive_playlists_apart():
    df = candidates(60)
    first = assemble(df, minutes=None, count=15, seed=1)
    df = df.copy()
    df["served_count"] = df["key"].isin(first["key"]).astype(int)
    second = assemble(df, minutes=None, count=15, seed=2)
    assert not set(first["key"]) & set(second["key"])


def test_rotation_reuses_the_least_served_once_everything_has_played():
    df = candidates(10, served_count=[3, 1, 2, 1, 3, 2, 1, 3, 2, 1])
    picked = assemble(df, minutes=None, count=4, seed=0)
    assert set(picked["served_count"]) == {1}


# ------------------------------------------------------------------ history

def test_serving_history_round_trips_through_state(tmp_path):
    lib = pd.DataFrame({
        "key": ["a", "b"], "title": ["A", "B"], "artist": ["x", "y"], "album": ["", ""],
        "isrc": [None, None], "source_id": [None, None], "bpm": [170.0, 170.0],
        "favorite": [False, False], "playlists": ["", ""],
    })
    first = catalog.connect(str(tmp_path / "one.db"))
    catalog.import_library(first, lib)
    catalog.record_served(first, ["a"], "2026-11-03")
    catalog.record_served(first, ["a"], "2026-11-04")
    state = tmp_path / "state.csv"
    catalog.export_state(first, str(state))

    second = catalog.connect(str(tmp_path / "two.db"))
    catalog.import_library(second, lib)
    catalog.import_state(second, str(state))
    rows = dict(second.execute("SELECT key, served_count FROM tracks").fetchall())
    assert rows == {"a": 2, "b": 0}


def test_old_database_is_migrated(tmp_path):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tracks (key TEXT PRIMARY KEY, title TEXT, artist TEXT, album TEXT,"
                " isrc TEXT, source_id TEXT, favorite INTEGER, playlists TEXT, bpm REAL,"
                " bpm_source TEXT, bpm_tried INTEGER)")
    con.commit()
    con.close()
    columns = {r[1] for r in catalog.connect(path).execute("PRAGMA table_info(tracks)")}
    assert {"duration_sec", "play_count", "added_at", "served_count", "last_served"} <= columns
