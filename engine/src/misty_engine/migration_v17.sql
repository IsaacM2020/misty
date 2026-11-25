-- v17: ways (step B, "Yours"): how she talks and behaves, as he asked ("call me boss", "be drier", "stop saying good
-- morning"). Same rules as cares: one active row per key, a changed way marks the old row 'replaced', a dropped one stays as
-- 'dropped'. Set only by chat (the `way` tool), or seeded from the default ways file when the table is empty.
CREATE TABLE IF NOT EXISTS ways (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,                 -- short name, 1-4 words, e.g. "address", "tone", "no good morning"
    kind TEXT NOT NULL DEFAULT 'other',   -- address | tone | habit | never_say | other
    text TEXT NOT NULL,                -- one sentence about how she talks or behaves, e.g. "Call him boss."
    value TEXT NOT NULL DEFAULT '',    -- address only: the bare word to call him, so code-made sentences can use it
    origin TEXT NOT NULL DEFAULT 'chat',  -- default | chat
    status TEXT NOT NULL DEFAULT 'active',   -- active | replaced | dropped
    replaced_by TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ways_active ON ways(status, key);
