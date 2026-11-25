-- v14: feedback on what she told him (Season 3, Day 6). `mistakes` keeps every miss together with what she knew at
-- that moment, so Day 7 can turn it into a lesson and later replay it to check she would now get it right.
ALTER TABLE told ADD COLUMN seen TEXT;               -- JSON: what the gate saw when it decided (idle, voice, counts, hour)

CREATE TABLE IF NOT EXISTS mistakes (
    id TEXT PRIMARY KEY,
    at REAL NOT NULL,
    kind TEXT NOT NULL,                -- down | ignored | missed | too_much
    told_id TEXT,                      -- the line it is about (none for missed / too_much)
    about TEXT NOT NULL DEFAULT '',    -- what it was about, in his words or hers
    note TEXT NOT NULL DEFAULT '',     -- his exact words, when he gave them
    snapshot TEXT NOT NULL,            -- JSON: what she knew then (the moment, the gate's inputs, her cares and follows)
    lesson_id TEXT,                    -- the lesson made from it (Day 7)
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS mistakes_open ON mistakes(status, at);
