-- Likes on shared playlists. One like per visitor per playlist: the voter is
-- a SHA-256 of the client address and the playlist id, never the address.
ALTER TABLE playlists ADD COLUMN likes INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS likes (
  slug       TEXT NOT NULL,
  voter      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (slug, voter)
);
CREATE INDEX IF NOT EXISTS playlists_likes ON playlists (likes DESC, created_at DESC);
