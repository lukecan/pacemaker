"""The CLI must name playlists exactly as the website does."""
import pytest

from pacemaker.names import playlist_name, playlist_seed

# Expected values computed by site/lib/names.js
CASES = [
    [
        [
            "GBUM70603730",
            "USSM19801941"
        ],
        170,
        "en",
        "Breathless Ballads · Golden Hour No. 4"
    ],
    [
        [
            "rehab|amy winehouse",
            "ğüşıöç|türkçe"
        ],
        171.5,
        "tr",
        "Ayak Uyduranlar · Altın Saat No. 76"
    ],
    [
        [
            "B",
            "A",
            "C"
        ],
        165,
        "tr",
        "Su Molası Sololar · Erkenci Kuş No. 23"
    ]
]


@pytest.mark.parametrize("keys,cadence,lang,expected", CASES)
def test_same_name_as_the_website(keys, cadence, lang, expected):
    assert playlist_name(playlist_seed(keys, cadence), lang) == expected
