"""SQLite track store.

The database is the source of truth. Providers only fill in missing BPM
values; it is never the other way round. BPM precedence is
``manual > library > provider``, so a value you corrected by hand is never
overwritten by a later run.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    key         TEXT PRIMARY KEY,
    title       TEXT,
    artist      TEXT,
    album       TEXT,
    isrc        TEXT,
    source_id   TEXT,
    favorite    INTEGER DEFAULT 0,
    playlists   TEXT,
    bpm         REAL,
    bpm_source  TEXT,
    bpm_tried   INTEGER DEFAULT 0,
    duration_sec INTEGER,
    play_count  INTEGER,
    added_at    TEXT,
    served_count INTEGER DEFAULT 0,
    last_served TEXT,
    origin      TEXT DEFAULT 'library',
    deezer_id   TEXT,
    seed        TEXT
);
CREATE INDEX IF NOT EXISTS tracks_bpm ON tracks (bpm);
"""

TRUSTED = ("manual", "library")
"""BPM sources a provider run must never overwrite."""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info(tracks)")}
    added = {  # columns introduced after the first release
        "duration_sec": "INTEGER", "play_count": "INTEGER", "added_at": "TEXT",
        "served_count": "INTEGER DEFAULT 0", "last_served": "TEXT",
        "origin": "TEXT DEFAULT 'library'", "deezer_id": "TEXT", "seed": "TEXT",
    }
    for name, kind in added.items():
        if name not in columns:
            con.execute(f"ALTER TABLE tracks ADD COLUMN {name} {kind}")
    con.commit()
    return con


def import_library(con: sqlite3.Connection, lib: pd.DataFrame) -> int:
    """Insert or refresh tracks. A BPM carried by the library file is kept
    unless a manual value already exists."""
    for row in lib.itertuples(index=False):
        bpm = None if pd.isna(row.bpm) else float(row.bpm)
        duration = _optional_int(getattr(row, "duration_sec", None))
        plays = _optional_int(getattr(row, "play_count", None))
        added_at = getattr(row, "added_at", None)
        added_at = None if added_at is None or pd.isna(added_at) else str(added_at)
        con.execute(
            "INSERT INTO tracks (key,title,artist,album,isrc,source_id,favorite,playlists,"
            "bpm,bpm_source,bpm_tried,duration_sec,play_count,added_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  origin='library', "
            "  favorite=excluded.favorite, playlists=excluded.playlists, "
            "  play_count=excluded.play_count, added_at=excluded.added_at, "
            "  duration_sec=COALESCE(excluded.duration_sec, tracks.duration_sec), "
            "  bpm=CASE WHEN tracks.bpm_source='manual' THEN tracks.bpm "
            "           WHEN excluded.bpm IS NOT NULL THEN excluded.bpm "
            "           ELSE tracks.bpm END, "
            "  bpm_source=CASE WHEN tracks.bpm_source='manual' THEN 'manual' "
            "                  WHEN excluded.bpm IS NOT NULL THEN 'library' "
            "                  ELSE tracks.bpm_source END",
            (row.key, row.title, row.artist, row.album, row.isrc, row.source_id,
             int(row.favorite), row.playlists, bpm,
             "library" if bpm is not None else None, 1 if bpm is not None else 0,
             duration, plays, added_at))
    con.commit()
    return int(con.execute("SELECT count(*) FROM tracks").fetchone()[0])


def _optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return int(value)


def record_served(con: sqlite3.Connection, keys: list[str], when: str) -> None:
    """Remember which tracks a playlist used, so later playlists can tell
    familiar tracks from fresh ones."""
    con.executemany(
        "UPDATE tracks SET served_count=COALESCE(served_count,0)+1, last_served=? WHERE key=?",
        [(when, k) for k in keys])
    con.commit()


def set_bpm(con: sqlite3.Connection, key: str, bpm: float | None, source: str,
            duration: int | None = None) -> None:
    """Record a provider result. Marks the track as tried either way, so a
    miss is not retried on every run. A duration is stored whenever the
    provider knew one, since the library export often does not carry it."""
    con.execute(
        "UPDATE tracks SET bpm=?, bpm_source=?, bpm_tried=1 "
        "WHERE key=? AND COALESCE(bpm_source,'') NOT IN ('manual','library')",
        (bpm, source, key))
    if duration:
        con.execute(
            "UPDATE tracks SET duration_sec=? WHERE key=? AND duration_sec IS NULL",
            (int(duration), key))


def import_bpm_csv(con: sqlite3.Connection, path: str) -> int:
    """Load hand-checked BPM values. These win over everything else."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    updated = 0
    for row in df.to_dict("records"):
        bpm = row.get("bpm")
        if bpm is None or pd.isna(bpm):
            continue
        if row.get("key"):
            cur = con.execute(
                "UPDATE tracks SET bpm=?, bpm_source='manual', bpm_tried=1 WHERE key=?",
                (float(bpm), row["key"]))
        else:
            cur = con.execute(
                "UPDATE tracks SET bpm=?, bpm_source='manual', bpm_tried=1 "
                "WHERE lower(title)=lower(?) AND lower(artist)=lower(?)",
                (float(bpm), row.get("title"), row.get("artist")))
        updated += cur.rowcount
    con.commit()
    return updated


def pending(con: sqlite3.Connection, limit: int | None = None) -> list[tuple]:
    """Tracks still needing a BPM, favorites first."""
    query = ("SELECT key,title,artist,isrc FROM tracks "
             "WHERE bpm IS NULL AND bpm_tried=0 ORDER BY favorite DESC, artist, title")
    if limit:
        query += f" LIMIT {int(limit)}"
    return con.execute(query).fetchall()


def reset_misses(con: sqlite3.Connection) -> int:
    """Forget earlier failed lookups so the next run tries them again."""
    cur = con.execute(
        "UPDATE tracks SET bpm_tried=0 WHERE bpm IS NULL "
        "AND COALESCE(bpm_source,'') NOT IN ('manual','library')")
    con.commit()
    return cur.rowcount


DISCOVERY_COLUMNS = ["key", "title", "artist", "album", "isrc", "deezer_id",
                     "duration_sec", "bpm", "seed"]


def add_discovered(con: sqlite3.Connection, row: dict) -> bool:
    """Store a song found by discovery. Songs already in the library are
    never turned into discoveries. Returns True if it was new."""
    cur = con.execute(
        "INSERT INTO tracks (key,title,artist,album,isrc,deezer_id,duration_sec,bpm,seed,"
        "origin,bpm_source,bpm_tried,favorite,playlists) "
        "VALUES (?,?,?,?,?,?,?,?,?,'discovery','deezer',1,0,'') "
        "ON CONFLICT(key) DO NOTHING",
        tuple(row.get(c) for c in DISCOVERY_COLUMNS))
    return cur.rowcount > 0


def known_deezer_ids(con: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in con.execute(
        "SELECT deezer_id FROM tracks WHERE deezer_id IS NOT NULL")}


def export_discovery(con: sqlite3.Connection, path: str) -> int:
    df = pd.read_sql(
        f"SELECT {','.join(DISCOVERY_COLUMNS)} FROM tracks WHERE origin='discovery' ORDER BY key", con)
    df.to_csv(path, index=False)
    return len(df)


def import_discovery(con: sqlite3.Connection, path: str) -> int:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"deezer_id": str, "isrc": str})
    added = 0
    for row in df.to_dict("records"):
        clean = {k: (None if (v is None or (isinstance(v, float) and pd.isna(v))) else v)
                 for k, v in row.items()}
        if clean.get("duration_sec") is not None:
            clean["duration_sec"] = int(clean["duration_sec"])
        added += add_discovered(con, clean)
    con.commit()
    return added


def all_tracks(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM tracks", con)


def export_state(con: sqlite3.Connection, path: str) -> int:
    """Dump what we know, including misses, so a later run can resume."""
    df = pd.read_sql(
        "SELECT key,title,artist,bpm,bpm_source,bpm_tried,duration_sec,"
        "served_count,last_served FROM tracks "
        "WHERE bpm IS NOT NULL OR bpm_tried=1 OR served_count>0 ORDER BY key", con)
    df.to_csv(path, index=False)
    return len(df)


def import_state(con: sqlite3.Connection, path: str) -> int:
    """Restore a previous dump without clobbering trusted values."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    restored = 0
    for row in df.to_dict("records"):
        bpm = row.get("bpm")
        bpm = None if bpm is None or pd.isna(bpm) else float(bpm)
        source = row.get("bpm_source")
        source = None if source is None or pd.isna(source) else str(source)
        duration = _optional_int(row.get("duration_sec"))
        served = _optional_int(row.get("served_count")) or 0
        last = row.get("last_served")
        last = None if last is None or pd.isna(last) else str(last)
        con.execute(
            "UPDATE tracks SET served_count=?, last_served=? WHERE key=?",
            (served, last, row["key"]))
        restored += con.execute(
            "UPDATE tracks SET bpm=?, bpm_source=?, bpm_tried=?, "
            "duration_sec=COALESCE(?, duration_sec) "
            "WHERE key=? AND COALESCE(bpm_source,'') != 'manual'",
            (bpm, source, int(row.get("bpm_tried") or 0), duration, row["key"])).rowcount
    con.commit()
    return restored


def export_catalog(con: sqlite3.Connection, path: str) -> int:
    """Write the static JSON the browser front-end reads."""
    df = pd.read_sql(
        "SELECT title,artist,album,isrc,source_id,bpm,favorite,duration_sec FROM tracks "
        "WHERE bpm IS NOT NULL ORDER BY bpm", con)
    tracks = [{
        "title": row.title,
        "artist": row.artist,
        "album": row.album or "",
        "isrc": row.isrc or "",
        "sourceId": row.source_id or "",
        "bpm": round(float(row.bpm), 1),
        "favorite": bool(row.favorite),
        "seconds": None if pd.isna(row.duration_sec) else int(row.duration_sec),
    } for row in df.itertuples(index=False)]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "count": len(tracks), "tracks": tracks}, fh,
                  ensure_ascii=False, separators=(",", ":"))
    return len(tracks)
