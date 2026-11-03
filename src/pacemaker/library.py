"""Load a music library from whatever the user can export.

Supported inputs:

* **Streaming transfer CSV** - TuneMyMusic, Soundiiz and similar exports.
* **Generic CSV** - any file with title and artist columns; ``bpm`` is used
  when the column is present.
* **iTunes / Apple Music ``Library.xml``** - exported from the macOS Music
  app. These often already carry a BPM tag, which saves a lookup entirely.

Every loader returns the same frame: ``key, title, artist, album, isrc,
source_id, bpm, favorite, playlists``.
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pandas as pd

#: Rows whose ``Type`` is one of these are songs. Transfer services also emit
#: ``Album`` and ``Artist`` rows, which are not tracks.
SONG_TYPES = {"favorite", "playlist", "song", "track", "liked"}

COLUMN_ALIASES = {
    "title": ["track name", "name", "title", "track", "song", "song name", "track title"],
    "artist": ["artist name", "artist", "artists", "album artist", "artist(s)"],
    "album": ["album", "album name"],
    "isrc": ["isrc"],
    "bpm": ["bpm", "tempo", "beats per minute"],
    "source_id": ["apple - id", "apple id", "track id", "spotify id", "id"],
    "type": ["type"],
    "playlist": ["playlist name", "playlist"],
}


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    found = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                found[canonical] = lowered[alias]
                break
    return found


def _coerce_bpm(value) -> float | None:
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return None
    return bpm if 30.0 < bpm < 300.0 else None


def make_key(title: str, artist: str, isrc=None) -> str:
    """ISRC identifies the exact recording; fall back to title + artist."""
    if isinstance(isrc, str) and isrc.strip():
        return isrc.strip().upper()
    return f"{str(title).strip().lower()}|{str(artist).strip().lower()}"


def _finalize(records: list[dict]) -> pd.DataFrame:
    if not records:
        raise ValueError("no songs found in this library file")
    df = pd.DataFrame(records)
    for col in ("title", "artist", "album", "isrc", "source_id", "bpm", "favorite", "playlist"):
        if col not in df:
            df[col] = None
    df["title"] = df["title"].fillna("").astype(str).str.strip()
    df["artist"] = df["artist"].fillna("").astype(str).str.strip()
    df["album"] = df["album"].fillna("").astype(str)
    df = df[df["title"] != ""].copy()
    df["favorite"] = df["favorite"].fillna(False).astype(bool)
    df["key"] = [make_key(t, a, i) for t, a, i in zip(df["title"], df["artist"], df["isrc"])]
    return df.groupby("key", sort=False).agg(
        title=("title", "first"),
        artist=("artist", "first"),
        album=("album", "first"),
        isrc=("isrc", "first"),
        source_id=("source_id", "first"),
        bpm=("bpm", "max"),
        favorite=("favorite", "any"),
        playlists=("playlist", lambda s: "; ".join(sorted({x for x in s if isinstance(x, str) and x}))),
    ).reset_index()


def load_csv(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    cols = _resolve_columns(raw)
    if "title" not in cols or "artist" not in cols:
        raise ValueError(
            "could not find title and artist columns; expected something like "
            "'Track name' / 'Artist name' or 'title' / 'artist'")
    if "type" in cols:
        kinds = raw[cols["type"]].fillna("").str.strip().str.lower()
        raw = raw[kinds.isin(SONG_TYPES)].copy()
    records = []
    for row in raw.to_dict("records"):
        kind = str(row.get(cols.get("type", ""), "") or "").strip().lower()
        playlist = row.get(cols["playlist"]) if "playlist" in cols else None
        records.append({
            "title": row.get(cols["title"]),
            "artist": row.get(cols["artist"]),
            "album": row.get(cols["album"]) if "album" in cols else None,
            "isrc": row.get(cols["isrc"]) if "isrc" in cols else None,
            "source_id": row.get(cols["source_id"]) if "source_id" in cols else None,
            "bpm": _coerce_bpm(row.get(cols["bpm"])) if "bpm" in cols else None,
            "favorite": kind in {"favorite", "liked"},
            "playlist": playlist if kind == "playlist" else None,
        })
    return _finalize(records)


def load_itunes_xml(path: str) -> pd.DataFrame:
    with open(path, "rb") as fh:
        plist = plistlib.load(fh)
    records = []
    for track in (plist.get("Tracks") or {}).values():
        if not isinstance(track, dict) or not track.get("Name"):
            continue
        if track.get("Track Type") == "Remote" and not track.get("Artist"):
            continue
        records.append({
            "title": track.get("Name"),
            "artist": track.get("Artist") or track.get("Album Artist"),
            "album": track.get("Album"),
            "isrc": None,
            "source_id": str(track.get("Track ID") or "") or None,
            "bpm": _coerce_bpm(track.get("BPM")),
            "favorite": bool(track.get("Loved")),
            "playlist": None,
        })
    return _finalize(records)


def load_library(path: str) -> pd.DataFrame:
    suffix = Path(path).suffix.lower()
    if suffix in {".xml", ".plist"}:
        return load_itunes_xml(path)
    return load_csv(path)
