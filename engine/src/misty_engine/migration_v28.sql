-- v28 (step E, E3): one row per source that has been mapped (or is being mapped). The topics themselves are in teach_topics.
CREATE TABLE IF NOT EXISTS teach_maps (
  source_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  status TEXT NOT NULL,                    -- queued | running | done | too_big | failed
  topics INTEGER NOT NULL DEFAULT 0,
  dropped INTEGER NOT NULL DEFAULT 0,      -- topics thrown away because their quote was not in the source
  loops INTEGER NOT NULL DEFAULT 0,        -- needs refused because they would make a loop
  merged INTEGER NOT NULL DEFAULT 0,
  usd REAL NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT '',
  why TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL
);
