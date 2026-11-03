"""Probe Deezer's artist search, related artists, top tracks and track detail."""
import json
import urllib.parse
import urllib.request


def get(path):
    req = urllib.request.Request("https://api.deezer.com" + path,
                                 headers={"User-Agent": "pacemaker-diagnostic/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


for name in ["The Strokes", "Red Hot Chili Peppers", "Yavuz Çetin"]:
    print(f"\n=== {name}")
    s = get("/search/artist?" + urllib.parse.urlencode({"q": name, "limit": 3}))
    for a in (s.get("data") or [])[:3]:
        print(f"  artist hit id={a.get('id')} name={a.get('name')!r} nb_fan={a.get('nb_fan')}")
    if not s.get("data"):
        print("  no artist hits", s.get("error"))
        continue
    aid = s["data"][0]["id"]
    rel = get(f"/artist/{aid}/related?limit=6")
    print(f"  related: total={rel.get('total')} error={rel.get('error')}")
    for a in (rel.get("data") or [])[:6]:
        print(f"    id={a.get('id')} name={a.get('name')!r} nb_fan={a.get('nb_fan')}")
    if rel.get("data"):
        rid = rel["data"][0]["id"]
        top = get(f"/artist/{rid}/top?limit=5")
        print(f"  top tracks of {rel['data'][0]['name']!r}: total={top.get('total')} error={top.get('error')}")
        for t in (top.get("data") or [])[:5]:
            print(f"    id={t.get('id')} title={t.get('title_short')!r} dur={t.get('duration')} "
                  f"isrc_in_top={t.get('isrc')!r} bpm_in_top={t.get('bpm')!r} "
                  f"album={(t.get('album') or {}).get('title')!r} keys={sorted(t.keys())[:12]}")
        if top.get("data"):
            full = get(f"/track/{top['data'][0]['id']}")
            print(f"  track detail: bpm={full.get('bpm')!r} isrc={full.get('isrc')!r} "
                  f"album={(full.get('album') or {}).get('title')!r} dur={full.get('duration')}")
