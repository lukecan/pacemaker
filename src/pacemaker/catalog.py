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
    bpm_tried   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tracks_bpm ON tracks (bpm);
"""

TRUSTED = ("manual", "library")
"""BPM sources a provider run must never overwrite."""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def import_library(con: sqlite3.Connection, lib: pd.DataFrame) -> int:
    """Insert or refresh tracks. A BPM carried by the library file is kept
    unless a manual value already exists."""
    for row in lib.itertuples(index=False):
        bpm = None if pd.isna(row.bpm) else float(row.bpm)
        con.execute(
            "INSERT INTO tracks (key,title,artist,album,isrc,source_id,favorite,playlists,"
            "bpm,bpm_source,bpm_tried) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  favorite=excluded.favorite, playlists=excluded.playlists, "
            "  bpm=CASE WHEN tracks.bpm_source='manual' THEN tracks.bpm "
            "           WHEN excluded.bpm IS NOT NULL THEN excluded.bpm "
            "           ELSE tracks.bpm END, "
            "  bpm_source=CASE WHEN tracks.bpm_source='manual' THEN 'manual' "
            "                  WHEN excluded.bpm IS NOT NULL THEN 'library' "
            "                  ELSE tracks.bpm_source END",
            (row.key, row.title, row.artist, row.album, row.isrc, row.source_id,
             int(row.favorite), row.playlists, bpm,
             "library" if bpm is not None else None, 1 if bpm is not None else 0))
    con.commit()
    return int(con.execute("SELECT count(*) FROM tracks").fetchone()[0])


def set_bpm(con: sqlite3.Connection, key: str, bpm: float | None, source: str) -> None:
    """Record a provider result. Marks the track as tried either way, so a
    miss is not retried on every run."""
    con.execute(
        "UPDATE tracks SET bpm=?, bpm_source=?, bpm_tried=1 "
        "WHERE key=? AND COALESCE(bpm_source,'') NOT IN ('manual','library')",
        (bpm, source, key))


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
    query = ("SELECT key,title,artist FROM tracks "
             "WHERE bpm IS NULL AND bpm_tried=0 ORDER BY favorite DESC, artist, title")
    if limit:
        query += f" LIMIT {int(limit)}"
    return con.execute(query).fetchall()


def all_tracks(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM tracks", con)


def export_state(con: sqlite3.Connection, path: str) -> int:
    """Dump what we know, including misses, so a later run can resume."""
    df = pd.read_sql(
        "SELECT key,title,artist,bpm,bpm_source,bpm_tried FROM tracks "
        "WHERE bpm IS NOT NULL OR bpm_tried=1 ORDER BY key", con)
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
        restored += con.execute(
            "UPDATE tracks SET bpm=?, bpm_source=?, bpm_tried=? "
            "WHERE key=? AND COALESCE(bpm_source,'') != 'manual'",
            (bpm, source, int(row.get("bpm_tried") or 0), row["key"])).rowcount
    con.commit()
    return restored


def export_catalog(con: sqlite3.Connection, path: str) -> int:
    """Write the static JSON the browser front-end reads."""
    df = pd.read_sql(
        "SELECT title,artist,album,isrc,source_id,bpm,favorite FROM tracks "
        "WHERE bpm IS NOT NULL ORDER BY bpm", con)
    tracks = [{
        "title": row.title,
        "artist": row.artist,
        "album": row.album or "",
        "isrc": row.isrc or "",
        "sourceId": row.source_id or "",
        "bpm": round(float(row.bpm), 1),
        "favorite": bool(row.favorite),
    } for row in df.itertuples(index=False)]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "count": len(tracks), "tracks": tracks}, fh,
                  ensure_ascii=False, separators=(",", ":"))
    return len(tracks)
