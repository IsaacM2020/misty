-- v42 (S9): every slate that was shown, as the exact JSON the page was given, so it can be reopened or replayed.
CREATE TABLE IF NOT EXISTS slates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'quick',
  json TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS slates_created ON slates(created_at);
