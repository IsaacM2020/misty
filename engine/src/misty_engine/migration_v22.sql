-- v22: protocols (step D, card D6). A protocol is a named list of moves he made by talking. One active row per key; an
-- old row is marked 'replaced', never deleted.
CREATE TABLE IF NOT EXISTS protocols (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    triggers TEXT NOT NULL DEFAULT '[]',     -- JSON list of phrases that start it
    steps TEXT NOT NULL DEFAULT '[]',        -- JSON list of {move, params}
    end_steps TEXT NOT NULL DEFAULT '[]',    -- JSON list of {move, params}: what "I'm done" runs
    status TEXT NOT NULL DEFAULT 'active',   -- active | replaced | dropped
    replaced_by TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    runs INTEGER NOT NULL DEFAULT 0,
    last_run_at REAL
);
CREATE INDEX IF NOT EXISTS protocols_key ON protocols(key, status);
