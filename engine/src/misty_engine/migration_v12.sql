-- v12: follows (Season 3, Day 4). A follow is a sentence Isaac said ("keep an eye on X") plus the plan Misty chose to
-- check it: which kind of source, what to ask it, how often, until when. Findings are what a check turned up.
CREATE TABLE IF NOT EXISTS follows (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,                -- his sentence, so it still makes sense next week
    source TEXT NOT NULL,              -- web | feed | mail
    query TEXT NOT NULL DEFAULT '',    -- web search words, or mail search words
    url TEXT,                          -- feed address (feed only)
    every_min INTEGER NOT NULL,
    until_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',   -- active | ended | stopped
    seen TEXT NOT NULL DEFAULT '[]',   -- ids of items already looked at (newest last)
    checks INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0, -- in a row; each one doubles the wait
    last_error TEXT,
    last_at REAL,
    next_at REAL NOT NULL,
    judged_day TEXT,                   -- SGT date of judged_n
    judged_n INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS follows_due ON follows(status, next_at);
CREATE TABLE IF NOT EXISTS follow_findings (
    id TEXT PRIMARY KEY,
    follow_id TEXT NOT NULL REFERENCES follows(id) ON DELETE CASCADE,
    at REAL NOT NULL,
    kind TEXT NOT NULL,                -- baseline (the first look) | new
    text TEXT NOT NULL,                -- what a source said, in plain words, naming the source
    source_name TEXT,
    url TEXT
);
CREATE INDEX IF NOT EXISTS follow_findings_by_follow ON follow_findings(follow_id, at);
