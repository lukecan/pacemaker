"""Run the real Deezer provider against well-known songs and report coverage."""
import time

from pacemaker.providers import Deezer

SONGS = [  # (title, artist, isrc or None)
    ("Rehab", "Amy Winehouse", "GBUM70603730"),
    ("Africa", "Toto", "USSM19801941"),
    ("Billie Jean", "Michael Jackson", "USSM19902991"),
    ("Mr. Brightside", "The Killers", "USIR20500155"),
    ("Seven Nation Army", "The White Stripes", "USVT10300001"),
    ("Aerodynamic", "Daft Punk", None),
    ("Can't Stop", "Red Hot Chili Peppers", None),
    ("Scar Tissue", "Red Hot Chili Peppers", None),
    ("Numb", "LINKIN PARK", None),
    ("In the End", "LINKIN PARK", None),
    ("Last Nite", "The Strokes", None),
    ("Reptilia", "The Strokes", None),
    ("Dean Town", "Vulfpeck", None),
    ("Little Lion Man", "Mumford & Sons", None),
    ("Purple Haze", "Jimi Hendrix", None),
    ("Alive", "Pearl Jam", None),
    ("1979", "The Smashing Pumpkins", None),
    ("Santeria", "Sublime", None),
    ("Get Lucky", "Daft Punk", None),
    ("Uptown Funk", "Mark Ronson", None),
    ("Don't Stop Me Now", "Queen", None),
    ("Everybody Wants to Rule the World", "Tears for Fears", None),
    ("Üsküdara Giderken", "Mustafa Özkent", None),
    ("Ağlama Değmez Hayat", "Kurban", None),
    ("Boşluk", "Büyük Ev Ablukada", None),
    ("Sevdim Seni Bir Kere", "Yavuz Çetin", None),
    ("Kaktüs", "Yavuz Çetin", None),
    ("Gesi Bağları", "Barış Manço", None),
    ("Islak Islak", "Cem Karaca", None),
    ("Tamirci Çırağı", "Cem Karaca", None),
]

provider = Deezer()
found = 0
with_duration = 0
for title, artist, isrc in SONGS:
    t0 = time.time()
    try:
        r = provider.lookup(title, artist, isrc)
    except Exception as e:  # noqa: BLE001
        r = {"bpm": None, "duration": None, "error": f"{type(e).__name__}: {e}"}
    found += r["bpm"] is not None
    with_duration += r.get("duration") is not None
    tag = "isrc" if isrc else "search"
    print(f"{('OK ' if r['bpm'] else '-- ')} [{tag:6}] {title} / {artist}: bpm={r['bpm']} "
          f"dur={r.get('duration')} {r.get('error', '')} ({time.time()-t0:.1f}s)")
    time.sleep(0.5)

print(f"\nBPM found for {found}/{len(SONGS)} ({100*found/len(SONGS):.0f}%), "
      f"duration for {with_duration}/{len(SONGS)}")
