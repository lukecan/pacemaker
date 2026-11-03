import plistlib

import pytest

from pacemaker.library import load_library, make_key

TRANSFER_CSV = """Track name,Artist name,Album,Playlist name,Type,ISRC,Apple - id
Rehab,Amy Winehouse,Back to Black,Library Songs,Favorite,GBUM70603730,1440856220
Zero,The Smashing Pumpkins,Mellon Collie,Library Songs,Favorite,USVI21200895,1455510869
Africa,Toto,Toto IV,Running,Playlist,USSM19801941,185717604
Toto IV,Toto,Toto IV,Library Albums,Album,,999
Toto,,,Library Artists,Artist,,888
"""

GENERIC_CSV = """title,artist,bpm
Song One,Band A,170
Song Two,Band B,not-a-number
Song Three,Band C,9999
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_transfer_csv_keeps_only_songs(tmp_path):
    lib = load_library(write(tmp_path, "lib.csv", TRANSFER_CSV))
    assert len(lib) == 3  # Album and Artist rows dropped
    assert set(lib["title"]) == {"Rehab", "Zero", "Africa"}


def test_favorites_and_playlists_are_flagged(tmp_path):
    lib = load_library(write(tmp_path, "lib.csv", TRANSFER_CSV)).set_index("title")
    assert bool(lib.loc["Rehab", "favorite"]) is True
    assert bool(lib.loc["Africa", "favorite"]) is False
    assert lib.loc["Africa", "playlists"] == "Running"


def test_isrc_becomes_the_key(tmp_path):
    lib = load_library(write(tmp_path, "lib.csv", TRANSFER_CSV)).set_index("title")
    assert lib.loc["Rehab", "key"] == "GBUM70603730"


def test_key_falls_back_to_title_and_artist():
    assert make_key("Rehab", "Amy Winehouse") == "rehab|amy winehouse"
    assert make_key("Rehab", "Amy Winehouse", "  ") == "rehab|amy winehouse"
    assert make_key("Rehab", "Amy Winehouse", "gbum70603730") == "GBUM70603730"


def test_generic_csv_reads_bpm_and_rejects_nonsense(tmp_path):
    lib = load_library(write(tmp_path, "songs.csv", GENERIC_CSV)).set_index("title")
    assert lib.loc["Song One", "bpm"] == 170.0
    assert lib.loc["Song Two", "bpm"] is None or lib.loc["Song Two", "bpm"] != lib.loc["Song Two", "bpm"]
    assert lib.loc["Song Three", "bpm"] != 9999.0  # out of plausible range


def test_csv_without_artist_column_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="title and artist"):
        load_library(write(tmp_path, "bad.csv", "foo,bar\n1,2\n"))


def test_itunes_xml_with_bpm_tags(tmp_path):
    plist = {"Tracks": {
        "101": {"Track ID": 101, "Name": "Rehab", "Artist": "Amy Winehouse",
                "Album": "Back to Black", "BPM": 144, "Loved": True},
        "102": {"Track ID": 102, "Name": "No BPM", "Artist": "Band"},
        "103": {"Track ID": 103, "Album": "Nameless"},
    }}
    path = tmp_path / "Library.xml"
    path.write_bytes(plistlib.dumps(plist))
    lib = load_library(str(path)).set_index("title")
    assert len(lib) == 2  # the track with no Name is skipped
    assert lib.loc["Rehab", "bpm"] == 144.0
    assert bool(lib.loc["Rehab", "favorite"]) is True


def test_duplicate_recordings_collapse(tmp_path):
    csv = ("Track name,Artist name,Album,Playlist name,Type,ISRC,Apple - id\n"
           "After the Storm,Mumford & Sons,Sigh No More,Library Songs,Favorite,GBUM70909104,1\n"
           "After the Storm,Mumford & Sons,Sigh No More (Deluxe),Running,Playlist,GBUM70909104,2\n")
    lib = load_library(write(tmp_path, "dup.csv", csv))
    assert len(lib) == 1
    assert bool(lib.iloc[0]["favorite"]) is True
    assert lib.iloc[0]["playlists"] == "Running"
