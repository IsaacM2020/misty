-- v30 (step E, E6): every answer key the checker looked at, and what it decided. A rejected key is logged here and never repaired.
CREATE TABLE IF NOT EXISTS teach_keylog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  kind TEXT NOT NULL,                      -- number | choice | term | points
  ok INTEGER NOT NULL,
  why TEXT NOT NULL DEFAULT '',
  cost REAL NOT NULL DEFAULT 0
);
