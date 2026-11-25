-- v20: the moves log (step D, card D1). Every typed move she makes is one row: what, how risky, whether she asked, his answer,
-- whether it was proved, and what undoes it. Params are scrubbed. call_id is unique, so a repeat runs once.
CREATE TABLE IF NOT EXISTS moves_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    call_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL,
    asked INTEGER NOT NULL DEFAULT 0,
    answer TEXT NOT NULL DEFAULT '',         -- yes | no | timeout | '' (not asked)
    verified INTEGER,                        -- 1 proved, 0 not proved, NULL never ran
    what TEXT NOT NULL DEFAULT '',
    undo TEXT NOT NULL DEFAULT '',           -- JSON: how to take it back, '' when it cannot be
    error TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'him'       -- him | text (params came from mail, a page or screen text)
);
CREATE INDEX IF NOT EXISTS moves_log_at ON moves_log(at);
