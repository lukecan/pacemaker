"""Discovery flow against recorded Deezer response shapes (2026-11-03)."""
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from pacemaker import catalog, discovery, providers
from pacemaker.matching import assemble, match_tracks

ARTISTS = {
    "The Strokes": [{"id": 569, "name": "The Strokes", "nb_fan": 1759344},
                    {"id": 11, "name": "The Rolling Stones", "nb_fan": 7120558}],
    "Yavuz Çetin": [{"id": 98033, "name": "Yavuz Çetin", "nb_fan": 11943}],
}
RELATED = {
    569: [{"id": 1182, "name": "Arctic Monkeys"}, {"id": 1652, "name": "Interpol"},
          {"id": 98033, "name": "Yavuz Çetin"}],
    98033: [{"id": 124344, "name": "Kurban"}, {"id": 1182, "name": "Arctic Monkeys"}],
}
TOP = {
    1182: [{"id": 14633829, "title_short": "505", "duration": 253, "artist": {"name": "Arctic Monkeys"},
            "album": {"title": "Favourite Worst Nightmare"}},
           {"id": 70322130, "title_short": "Do I Wanna Know?", "duration": 272,
            "artist": {"name": "Arctic Monkeys"}, "album": {"title": "AM"}}],
    1652: [{"id": 501, "title_short": "Evil", "duration": 216, "artist": {"name": "Interpol"},
            "album": {"title": "Antics"}}],
    124344: [{"id": 66413241, "title_short": "Yalan", "duration": 205, "artist": {"name": "Kurban"},
              "album": {"title": "Sert"}},
             {"id": 1947271187, "title_short": "Damla", "duration": 233, "artist": {"name": "Kurban"},
              "album": {"title": "Damla"}}],
}
DETAIL = {
    14633829: {"bpm": 140.2, "isrc": "GBCEL0700074"},
    70322130: {"bpm": 85.0, "isrc": "GBCEL1300362"},
    501: {"bpm": 171.0, "isrc": "USMTD0400042"},
    66413241: {"bpm": 0, "isrc": "TRA160600011"},        # Deezer has no tempo
    1947271187: {"bpm": 169.5, "isrc": "TRA162200623"},
}


@pytest.fixture
def fake_deezer(monkeypatch):
    calls = []

    def fake(url, headers=None, timeout=20):
        calls.append(url)
        parsed = urlparse(url)
        path = parsed.path
        if path == "/search/artist":
            return {"data": ARTISTS.get(parse_qs(parsed.query)["q"][0], [])}
        if path.endswith("/related"):
            return {"data": RELATED.get(int(path.split("/")[2]), [])}
        if path.endswith("/top"):
            return {"data": TOP.get(int(path.split("/")[2]), [])}
        if path.startswith("/track/"):
            tid = int(path.split("/")[2])
            for tracks in TOP.values():
                for t in tracks:
                    if t["id"] == tid:
                        return {**t, **DETAIL[tid]}
            return {"error": {"code": 800}}
        raise AssertionError(url)

    monkeypatch.setattr(providers, "_get_json", fake)
    return calls


def library_with(rows):
    return pd.DataFrame(rows, columns=["key", "title", "artist", "album", "isrc", "source_id",
                                       "bpm", "favorite", "playlists"])


@pytest.fixture
def con(tmp_path):
    c = catalog.connect(str(tmp_path / "t.db"))
    catalog.import_library(c, library_with([
        ["s1", "Last Nite", "The Strokes", "", None, None, 104.0, True, "Replay 2026"],
        ["s2", "Reptilia", "The Strokes", "", None, None, 158.0, True, ""],
        ["y1", "Sevdim Seni Bir Kere", "Yavuz Çetin", "", None, None, None, True, ""],
        # already owns one Arctic Monkeys song that Deezer will suggest
        ["a1", "505", "Arctic Monkeys", "", "GBCEL0700074", None, 140.2, False, "Soul 45"],
    ]))
    return c


def test_seeds_weight_play_history(con):
    seeds = discovery.seed_artists(catalog.all_tracks(con))
    assert seeds[0] == ("The Strokes", 6.0)  # one Replay song counts five times


def test_artist_lookup_prefers_the_right_name(fake_deezer):
    assert providers.deezer_find_artist("The Strokes")["id"] == 569


def test_discover_adds_only_songs_you_do_not_have(fake_deezer, con):
    stats = discovery.discover(con, delay=0, log=lambda *_: None)
    found = catalog.all_tracks(con).query("origin == 'discovery'")
    titles = set(found["title"])
    assert "505" not in titles                     # already in the library
    assert {"Do I Wanna Know?", "Evil", "Damla", "Yalan"} <= titles
    assert found.set_index("title").loc["Yalan", "bpm"] is None or \
        pd.isna(found.set_index("title").loc["Yalan", "bpm"])
    assert stats["owned"] >= 1


def test_new_artists_outrank_ones_you_already_play():
    seeds = [("The Strokes", 9.0), ("Pearl Jam", 4.0)]
    related = {
        "The Strokes": [{"id": 1, "name": "Pearl Jam"}, {"id": 2, "name": "Interpol"},
                        {"id": 3, "name": "Cage The Elephant"}],
        "Pearl Jam": [{"id": 3, "name": "Cage The Elephant"}, {"id": 4, "name": "Soundgarden"}],
    }
    owned = {"the strokes", "pearl jam", "soundgarden"}
    order = [name for _, name, _, _ in discovery.rank_similar(seeds, related, owned)]
    assert order[0] == "Cage The Elephant"         # suggested twice, new to you
    assert order.index("Interpol") < order.index("Soundgarden")  # new beats owned
    assert order[-1] == "Pearl Jam"                # your own main artist comes last


def test_second_run_does_not_refetch(fake_deezer, con):
    discovery.discover(con, delay=0, log=lambda *_: None)
    before = len([c for c in fake_deezer if "/track/" in c])
    discovery.discover(con, delay=0, log=lambda *_: None)
    after = len([c for c in fake_deezer if "/track/" in c])
    assert after == before


def test_discoveries_survive_export_and_import(fake_deezer, con, tmp_path):
    discovery.discover(con, delay=0, log=lambda *_: None)
    out = tmp_path / "discovery.csv"
    n = catalog.export_discovery(con, str(out))
    fresh = catalog.connect(str(tmp_path / "other.db"))
    assert catalog.import_discovery(fresh, str(out)) == n
    assert catalog.known_deezer_ids(fresh) == catalog.known_deezer_ids(con) - {None}


def test_library_import_reclaims_a_song_you_later_added(fake_deezer, con):
    discovery.discover(con, delay=0, log=lambda *_: None)
    key = catalog.all_tracks(con).query("title == 'Evil'")["key"].iloc[0]
    catalog.import_library(con, library_with([[key, "Evil", "Interpol", "", None, None, None, False, ""]]))
    assert catalog.all_tracks(con).set_index("key").loc[key, "origin"] == "library"


def test_new_share_comes_from_discoveries(fake_deezer, con):
    discovery.discover(con, delay=0, log=lambda *_: None)
    df = catalog.all_tracks(con)
    lib_rows = pd.DataFrame({"key": [f"l{i}" for i in range(20)], "title": [f"t{i}" for i in range(20)],
                             "artist": ["x"] * 20, "bpm": [170.0] * 20, "favorite": [True] * 20,
                             "origin": ["library"] * 20, "served_count": [0] * 20})
    pool = match_tracks(pd.concat([df, lib_rows], ignore_index=True), 170, 3)
    playlist = assemble(pool, minutes=None, count=10, fresh=0.3)
    new = playlist[playlist["origin"] == "discovery"]
    # Evil (171), Damla (169.5) and Do I Wanna Know? (85, half-time) fit
    assert len(new) == 3
    assert set(new["title"]) == {"Evil", "Damla", "Do I Wanna Know?"}


def test_no_fresh_means_no_discoveries(fake_deezer, con):
    discovery.discover(con, delay=0, log=lambda *_: None)
    pool = match_tracks(catalog.all_tracks(con), 170, 3)
    for fresh in (None, 0):
        playlist = assemble(pool, minutes=None, count=10, fresh=fresh)
        assert not (playlist.get("origin", pd.Series(dtype=str)) == "discovery").any()


@pytest.mark.parametrize("raw,expected", [
    ("Mumford & Sons", "Mumford & Sons"),
    ("Daryl Hall & John Oates", "Daryl Hall & John Oates"),
    ("Gorillaz feat. De La Soul", "Gorillaz"),
    ("Mark Ronson, Bruno Mars", "Mark Ronson"),
])
def test_seed_names_keep_band_names_whole(raw, expected):
    assert discovery.seed_name(raw) == expected
