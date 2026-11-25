-- v16: the morning brief (Season 3, Day 8). One row a day: what she said and what she can open for the thing she
-- suggested working on ("let's go" opens exactly these, nothing else).
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL UNIQUE,          -- YYYY-MM-DD, SGT
    at REAL NOT NULL,
    say TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',     -- the one thing to work on, in a few words
    opens TEXT NOT NULL DEFAULT '[]',  -- JSON: [{id, kind: link|folder, target, label}]
    facts TEXT NOT NULL DEFAULT '{}'   -- JSON: what she knew when she wrote it (kept for checking and for lessons)
);
