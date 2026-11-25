-- v15: lessons (Season 3, Day 7). A lesson is one short plain sentence, written from his feedback, that she reads every
-- time she decides whether to speak up. `replays` keeps the result of every check against the saved mistakes.
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',     -- active | replaced | dropped
    source TEXT NOT NULL DEFAULT 'feedback',   -- feedback | chat
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS lessons_active ON lessons(status, created_at);

CREATE TABLE IF NOT EXISTS replays (
    id TEXT PRIMARY KEY,
    at REAL NOT NULL,
    lesson_id TEXT,
    verdict TEXT NOT NULL,             -- kept | rejected | dropped
    total INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT ''    -- JSON: the cases that failed
);

ALTER TABLE held ADD COLUMN checked INTEGER NOT NULL DEFAULT 0;   -- the lessons have already looked at this one
