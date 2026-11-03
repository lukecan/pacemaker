import pandas as pd

from pacemaker import catalog


def library_frame(rows):
    return pd.DataFrame(rows, columns=["key", "title", "artist", "album", "isrc",
                                       "source_id", "bpm", "favorite", "playlists"])


SONGS = library_frame([
    ["k1", "Song One", "Band A", "Album", None, None, None, True, ""],
    ["k2", "Song Two", "Band B", "Album", None, None, 128.0, False, ""],
])


def test_library_bpm_is_kept_and_marked(tmp_path):
    con = catalog.connect(str(tmp_path / "t.db"))
    assert catalog.import_library(con, SONGS) == 2
    rows = dict(con.execute("SELECT key, bpm_source FROM tracks").fetchall())
    assert rows == {"k1": None, "k2": "library"}
    assert catalog.pending(con) == [("k1", "Song One", "Band A")]


def test_provider_never_overwrites_library_or_manual(tmp_path):
    con = catalog.connect(str(tmp_path / "t.db"))
    catalog.import_library(con, SONGS)
    catalog.set_bpm(con, "k2", 999.0, "deezer")
    assert con.execute("SELECT bpm FROM tracks WHERE key='k2'").fetchone()[0] == 128.0

    catalog.set_bpm(con, "k1", 170.0, "deezer")
    con.commit()
    manual = tmp_path / "manual.csv"
    manual.write_text("key,bpm\nk1,174\n", encoding="utf-8")
    assert catalog.import_bpm_csv(con, str(manual)) == 1
    catalog.set_bpm(con, "k1", 100.0, "deezer")
    assert con.execute("SELECT bpm FROM tracks WHERE key='k1'").fetchone()[0] == 174.0


def test_a_miss_is_remembered_so_it_is_not_retried(tmp_path):
    con = catalog.connect(str(tmp_path / "t.db"))
    catalog.import_library(con, SONGS)
    catalog.set_bpm(con, "k1", None, "deezer")
    con.commit()
    assert catalog.pending(con) == []


def test_state_round_trip_preserves_misses(tmp_path):
    first = catalog.connect(str(tmp_path / "a.db"))
    catalog.import_library(first, SONGS)
    catalog.set_bpm(first, "k1", None, "deezer")
    first.commit()
    state = tmp_path / "state.csv"
    assert catalog.export_state(first, str(state)) == 2

    second = catalog.connect(str(tmp_path / "b.db"))
    catalog.import_library(second, SONGS)
    assert catalog.import_state(second, str(state)) >= 1
    assert catalog.pending(second) == []


def test_catalog_json_only_includes_tracks_with_bpm(tmp_path):
    import json
    con = catalog.connect(str(tmp_path / "t.db"))
    catalog.import_library(con, SONGS)
    out = tmp_path / "catalog.json"
    assert catalog.export_catalog(con, str(out)) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["tracks"][0]["bpm"] == 128.0
