"""BPM providers.

``lookup`` returns ``{"bpm": float | None, "duration": int | None}``. The
duration matters because most library exports do not carry one, and without
it a playlist cannot be built to a target length.

HTTP and parsing are kept separate so the payload handling is unit-testable
without network access.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

UA = "pacemaker/0.1 (+https://github.com/lukecan/pacemaker)"

_PAREN = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring|with)\s+.*$", re.I)
_DASH_SUFFIX = re.compile(r"\s+-\s+(remaster(ed)?|live|single|radio|album|mono|stereo|demo|version|mix)\b.*$", re.I)


def normalize_title(title: str) -> str:
    """Strip edition/remaster/feat noise that breaks catalogue lookups."""
    t = _PAREN.sub("", str(title))
    t = _DASH_SUFFIX.sub("", t)
    t = _FEAT.sub("", t)
    return " ".join(t.split()).strip() or str(title).strip()


def normalize_artist(artist: str) -> str:
    a = _FEAT.sub("", str(artist))
    a = re.split(r"\s*[,&/]\s*|\s+(?:x|vs\.?)\s+", a, maxsplit=1)[0]
    return " ".join(a.split()).strip() or str(artist).strip()


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()


def pick_best(candidates: list[dict], title: str, artist: str, threshold: float = 0.72) -> dict | None:
    """Choose the candidate that actually matches; reject weak matches.

    Each candidate: {"title": ..., "artist": ..., "bpm": float|None}
    """
    best, best_score = None, 0.0
    for c in candidates:
        if not c.get("bpm"):
            continue
        score = 0.65 * _sim(c.get("title", ""), title) + 0.35 * _sim(c.get("artist", ""), artist)
        if score > best_score:
            best, best_score = c, score
    return best if best and best_score >= threshold else None


def _as_float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 30.0 < f < 300.0 else None


def _get_json(url: str, headers: dict | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# ---------------------------------------------------------------- GetSongBPM

def parse_getsongbpm(payload) -> list[dict]:
    """Accept both documented shapes: a bare list, or {"search": [...]}."""
    items = payload.get("search") if isinstance(payload, dict) else payload
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        a = it.get("artist")
        if isinstance(a, list):
            a = a[0] if a else {}
        name = a.get("name", "") if isinstance(a, dict) else str(a or "")
        out.append({"title": it.get("title", ""), "artist": name,
                    "bpm": _as_float(it.get("tempo")), "id": it.get("id"),
                    "duration": None})
    return out


class GetSongBPM:
    """https://getsongbpm.com/api - free, requires an API key.

    Their terms require a visible backlink to getsongbpm.com wherever the data
    is shown, so keep the link in the README and in any UI built on this.
    """
    name = "getsongbpm"
    needs_key = True

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GETSONGBPM_API_KEY")
        if not self.api_key:
            raise RuntimeError("GETSONGBPM_API_KEY is not set")

    def lookup(self, title: str, artist: str, isrc: str | None = None) -> dict:
        t, a = normalize_title(title), normalize_artist(artist)
        q = urllib.parse.urlencode({"type": "both", "lookup": f"song:{t} artist:{a}", "limit": "10"})
        url = f"https://api.getsongbpm.com/search/?{q}"
        payload = _get_json(url, {"X-API-KEY": self.api_key})
        hit = pick_best(parse_getsongbpm(payload), t, a)
        return {"bpm": hit["bpm"], "duration": None} if hit else {"bpm": None, "duration": None}


# -------------------------------------------------------------------- Deezer

def parse_deezer_search(payload) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        art = it.get("artist") or {}
        duration = it.get("duration")
        try:
            duration = int(duration) if duration else None
        except (TypeError, ValueError):
            duration = None
        album = it.get("album") or {}
        out.append({"title": it.get("title_short") or it.get("title", ""),
                    "artist": art.get("name", "") if isinstance(art, dict) else str(art),
                    "bpm": _as_float(it.get("bpm")), "id": it.get("id"),
                    "duration": duration if duration and 10 <= duration <= 1800 else None,
                    "isrc": it.get("isrc") or None,
                    "album": album.get("title", "") if isinstance(album, dict) else ""})
    return out


class Deezer:
    """api.deezer.com - no API key needed.

    Looks a track up by ISRC first: one request, and it identifies the exact
    recording. Without an ISRC it falls back to a plain text search. Search
    results never carry ``bpm``, so the closest few hits are opened as full
    track objects.

    Deezer reports ``bpm = 0`` for some tracks; those count as not found.
    The advanced ``track:"..." artist:"..."`` search syntax is deliberately
    not used: it returns no results through this endpoint.
    """
    name = "deezer"
    needs_key = False

    def __init__(self, probe: int = 3):
        self.probe = probe

    def _track(self, ref) -> dict | None:
        full = _get_json(f"https://api.deezer.com/track/{ref}")
        if not isinstance(full, dict) or full.get("error"):
            return None
        parsed = parse_deezer_search([full])
        return parsed[0] if parsed else None

    def lookup(self, title: str, artist: str, isrc: str | None = None) -> dict:
        if isinstance(isrc, str) and isrc.strip():
            hit = self._track(f"isrc:{urllib.parse.quote(isrc.strip().upper())}")
            if hit and hit["bpm"]:
                return {"bpm": hit["bpm"], "duration": hit.get("duration")}
            known_duration = hit.get("duration") if hit else None
        else:
            known_duration = None

        t, a = normalize_title(title), normalize_artist(artist)
        q = urllib.parse.urlencode({"q": f"{t} {a}".strip(), "limit": "10"})
        cands = parse_deezer_search(_get_json(f"https://api.deezer.com/search?{q}"))
        ranked = sorted(cands, key=lambda c: -(0.65 * _sim(c["title"], t) + 0.35 * _sim(c["artist"], a)))
        if known_duration is None and ranked and pick_best([{**ranked[0], "bpm": 1.0}], t, a):
            known_duration = ranked[0].get("duration")
        for cand in ranked[: self.probe]:
            # only open hits that plausibly are this song
            if not cand.get("id") or not pick_best([{**cand, "bpm": 1.0}], t, a):
                continue
            full = self._track(cand["id"])
            if full and full["bpm"]:
                return {"bpm": full["bpm"], "duration": full.get("duration") or cand.get("duration")}
        return {"bpm": None, "duration": known_duration}


# ------------------------------------------------------ Deezer discovery

def deezer_find_artist(name: str) -> dict | None:
    """Best artist match for a name, or None. Picks by name similarity, then
    by fan count, so "The Strokes" does not resolve to "The Rolling Stones"."""
    q = urllib.parse.urlencode({"q": name, "limit": "5"})
    data = _get_json(f"https://api.deezer.com/search/artist?{q}")
    hits = [h for h in (data.get("data") if isinstance(data, dict) else None) or []
            if isinstance(h, dict) and h.get("id")]
    if not hits:
        return None
    scored = sorted(hits, key=lambda h: (-_sim(h.get("name", ""), name), -(h.get("nb_fan") or 0)))
    best = scored[0]
    return best if _sim(best.get("name", ""), name) >= 0.8 else None


def deezer_related(artist_id, limit: int = 20) -> list[dict]:
    data = _get_json(f"https://api.deezer.com/artist/{artist_id}/related?limit={int(limit)}")
    return [a for a in (data.get("data") if isinstance(data, dict) else None) or []
            if isinstance(a, dict) and a.get("id")]


def deezer_top_tracks(artist_id, limit: int = 10) -> list[dict]:
    data = _get_json(f"https://api.deezer.com/artist/{artist_id}/top?limit={int(limit)}")
    return parse_deezer_search(data)


def deezer_track(track_id) -> dict | None:
    """Full track: the only Deezer object that carries ``bpm`` and ``isrc``."""
    data = _get_json(f"https://api.deezer.com/track/{track_id}")
    if not isinstance(data, dict) or data.get("error"):
        return None
    parsed = parse_deezer_search([data])
    return parsed[0] if parsed else None


PROVIDERS = {"getsongbpm": GetSongBPM, "deezer": Deezer}


def build(names: str):
    """``"deezer,getsongbpm"`` -> ``[Deezer(), GetSongBPM()]``.

    A provider that cannot initialise (usually a missing API key) is skipped
    with a note rather than failing the run.
    """
    chain, skipped = [], []
    for name in [x.strip() for x in names.split(",") if x.strip()]:
        cls = PROVIDERS.get(name)
        if cls is None:
            raise SystemExit(f"unknown provider: {name} (available: {', '.join(PROVIDERS)})")
        try:
            chain.append(cls())
        except RuntimeError as exc:
            skipped.append(f"{name}: {exc}")
    if not chain:
        raise SystemExit("no usable provider -> " + "; ".join(skipped))
    for note in skipped:
        print(f"skipped {note}")
    return chain
