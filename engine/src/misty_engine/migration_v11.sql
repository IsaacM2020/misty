-- v11: what Misty cares about (Season 3, Day 3). Goals, interests, people: whatever Isaac says matters to him.
-- Set only by chat. One active row per key. weight: 3 matters a lot, 2 matters, 1 a little, 0 never bring it up.
CREATE TABLE IF NOT EXISTS cares (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,                 -- short topic, 1-4 words, e.g. "cricket", "ib math hl", "arjun"
    kind TEXT NOT NULL DEFAULT 'other',   -- goal | interest | person | other (a label for display, nothing branches on it)
    text TEXT NOT NULL,                -- what he said, as one sentence, so it still makes sense next month
    weight INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'active',   -- active | replaced | dropped
    replaced_by TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS cares_active ON cares(status, key);
