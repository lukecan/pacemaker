"""Request flow for the Deezer provider, replayed from real responses.

The first version only had parser tests and shipped with a query syntax that
Deezer answers with zero hits, so every lookup silently failed. These tests
pin the actual sequence of requests against recorded API behaviour.
"""
import pytest

from pacemaker import providers

# Recorded 2026-11-03 from api.deezer.com (trimmed to the fields used).
TRACKS = {
    "isrc:GBUM70603730": {"id": 2176852, "title_short": "Rehab", "bpm": 145.6, "duration": 212,
                          "artist": {"name": "Amy Winehouse"}},
    "isrc:USIR20500155": {"id": 689411472, "title_short": "Mr. Brightside", "bpm": 0, "duration": 279,
                          "artist": {"name": "The Killers"}},
    "isrc:XX0000000000": {"error": {"type": "DataException", "message": "no data", "code": 800}},
    "1079668": {"id": 1079668, "title_short": "Africa", "bpm": 92.49, "duration": 296,
                "artist": {"name": "Toto"}},
    "3157689": {"id": 3157689, "title_short": "Aerodynamite", "bpm": 122, "duration": 468,
                "artist": {"name": "Daft Punk"}},
    "3089386": {"id": 3089386, "title_short": "Aerodynamic", "bpm": 123.4, "duration": 212,
                "artist": {"name": "Daft Punk"}},
}
SEARCH = {
    "Africa Toto": [{"id": 1079668, "title_short": "Africa", "duration": 296, "artist": {"name": "Toto"}}],
    # The wrong song ranks first for this query on Deezer.
    "Aerodynamic Daft Punk": [
        {"id": 3157689, "title_short": "Aerodynamite", "duration": 468, "artist": {"name": "Daft Punk"}},
        {"id": 3089386, "title_short": "Aerodynamic", "duration": 212, "artist": {"name": "Daft Punk"}},
    ],
}


@pytest.fixture
def deezer(monkeypatch):
    calls = []

    def fake_get_json(url, headers=None, timeout=20):
        calls.append(url)
        if "/search?" in url:
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(url).query)["q"][0]
            assert 'track:"' not in q, "advanced search syntax returns nothing on Deezer"
            return {"data": SEARCH.get(q, []), "total": len(SEARCH.get(q, []))}
        ref = url.rsplit("/track/", 1)[1].replace("%3A", ":")
        return TRACKS.get(ref, {"error": {"code": 800}})

    monkeypatch.setattr(providers, "_get_json", fake_get_json)
    return providers.Deezer(), calls


def test_isrc_lookup_takes_one_request(deezer):
    provider, calls = deezer
    assert provider.lookup("Rehab", "Amy Winehouse", "GBUM70603730") == {"bpm": 145.6, "duration": 212}
    assert len(calls) == 1 and "isrc:" in calls[0].replace("%3A", ":")


def test_plain_search_then_full_track_when_no_isrc(deezer):
    provider, calls = deezer
    assert provider.lookup("Africa", "Toto") == {"bpm": 92.49, "duration": 296}
    assert any("/search?" in c for c in calls) and any("/track/1079668" in c for c in calls)


def test_similarly_named_wrong_song_is_skipped(deezer):
    provider, _ = deezer
    assert provider.lookup("Aerodynamic", "Daft Punk")["bpm"] == 123.4


def test_zero_bpm_counts_as_not_found_but_keeps_duration(deezer):
    provider, _ = deezer
    assert provider.lookup("Mr. Brightside", "The Killers", "USIR20500155") == {"bpm": None, "duration": 279}


def test_unknown_isrc_falls_back_to_search(deezer):
    provider, calls = deezer
    assert provider.lookup("Africa", "Toto", "XX0000000000")["bpm"] == 92.49
    assert any("/search?" in c for c in calls)


def test_nothing_found_returns_empty_result(deezer):
    provider, _ = deezer
    assert provider.lookup("No Such Song", "Nobody") == {"bpm": None, "duration": None}


def test_retry_misses_resets_failed_lookups(tmp_path):
    import pandas as pd
    from pacemaker import catalog
    con = catalog.connect(str(tmp_path / "t.db"))
    catalog.import_library(con, pd.DataFrame({
        "key": ["a", "b"], "title": ["A", "B"], "artist": ["x", "y"], "album": ["", ""],
        "isrc": [None, None], "source_id": [None, None], "bpm": [None, 128.0],
        "favorite": [False, False], "playlists": ["", ""]}))
    catalog.set_bpm(con, "a", None, "deezer")
    con.commit()
    assert catalog.pending(con) == []
    assert catalog.reset_misses(con) == 1
    assert [row[0] for row in catalog.pending(con)] == ["a"]
