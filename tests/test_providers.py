from pacemaker.providers import (
    normalize_title, normalize_artist, pick_best,
    parse_getsongbpm, parse_deezer_search,
)


def test_normalize():
    assert normalize_title("A-Six (Original Long Version)") == "A-Six"
    assert normalize_title("Zero - Remastered 2012") == "Zero"
    assert normalize_title("Song (feat. X)") == "Song"
    assert normalize_title("(Only Parens)") == "(Only Parens)"  # never empties
    assert normalize_artist("Mumford & Sons") == "Mumford"
    assert normalize_artist("Artist feat. Other") == "Artist"


def test_parse_getsongbpm_both_shapes():
    song = {"id": "1", "title": "Rehab", "tempo": "144",
            "artist": [{"name": "Amy Winehouse"}]}
    assert parse_getsongbpm({"search": [song]})[0]["bpm"] == 144.0
    assert parse_getsongbpm([song])[0]["artist"] == "Amy Winehouse"
    assert parse_getsongbpm({"search": "No results"}) == []
    assert parse_getsongbpm([{"title": "x", "tempo": 0, "artist": []}])[0]["bpm"] is None


def test_parse_deezer():
    p = {"data": [{"id": 7, "title_short": "Rehab", "bpm": 144.9,
                   "artist": {"name": "Amy Winehouse"}}]}
    assert parse_deezer_search(p)[0]["bpm"] == 144.9
    assert parse_deezer_search({"data": []}) == []


def test_pick_best_rejects_wrong_song():
    cands = [{"title": "Totally Other Thing", "artist": "Nobody", "bpm": 170.0}]
    assert pick_best(cands, "Rehab", "Amy Winehouse") is None
    good = [{"title": "Rehab", "artist": "Amy Winehouse", "bpm": 144.0}]
    assert pick_best(good, "Rehab", "Amy Winehouse")["bpm"] == 144.0
