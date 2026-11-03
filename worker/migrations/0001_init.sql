-- Shared song -> BPM cache. Metadata comes from Deezer, never from uploads.
CREATE TABLE IF NOT EXISTS songs (
  key        TEXT PRIMARY KEY,       -- ISRC, or "title|artist" when there is none
  title      TEXT,
  artist     TEXT,
  album      TEXT,
  isrc       TEXT,
  deezer_id  TEXT,
  bpm        REAL,                   -- NULL when Deezer has no tempo
  duration   INTEGER,
  status     TEXT NOT NULL,          -- 'found' | 'missing'
  checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS songs_deezer ON songs (deezer_id);

-- Artist search result and related artists, per normalised name.
CREATE TABLE IF NOT EXISTS artists (
  key        TEXT PRIMARY KEY,
  deezer_id  TEXT,
  name       TEXT,
  related    TEXT,                   -- JSON [{id, name}]
  checked_at TEXT NOT NULL
);

-- Top tracks per Deezer artist id.
CREATE TABLE IF NOT EXISTS artist_top (
  deezer_id  TEXT PRIMARY KEY,
  tracks     TEXT NOT NULL,          -- JSON [{id, title, artist, album, duration}]
  checked_at TEXT NOT NULL
);

-- Playlists people chose to share.
CREATE TABLE IF NOT EXISTS playlists (
  slug       TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  lang       TEXT NOT NULL,
  cadence    REAL NOT NULL,
  minutes    INTEGER NOT NULL,
  new_count  INTEGER NOT NULL,
  tracks     TEXT NOT NULL,          -- JSON
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS playlists_cadence ON playlists (cadence, created_at);

-- Shares per client per day, to keep the gallery from being flooded.
CREATE TABLE IF NOT EXISTS share_quota (
  bucket     TEXT PRIMARY KEY,
  n          INTEGER NOT NULL
);
