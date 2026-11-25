-- v8: class notes (2026-10-23). A class is a session of transcript segments (text only, never audio), his typed
-- notes and the notes Misty makes after it. Segments are deleted 6 months on; sessions, notes and actions stay.
CREATE TABLE IF NOT EXISTS class_sessions (
    id TEXT PRIMARY KEY,               -- cs_<hex8>
    subject TEXT NOT NULL,
    title TEXT NOT NULL,
    cal_event_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    last_heard_at REAL,                -- the last segment's arrival, for closing abandoned sessions
    status TEXT NOT NULL DEFAULT 'live'
        CHECK (status IN ('live','paused','ended','noting','noted','too_little','failed')),
    his_notes TEXT NOT NULL DEFAULT '',
    notes_json TEXT,
    cost REAL NOT NULL DEFAULT 0,
    gaps TEXT NOT NULL DEFAULT '[]',   -- JSON [{from, to, reason}], seconds from the start
    pruned INTEGER NOT NULL DEFAULT 0  -- 1 once the transcript is gone
);
CREATE INDEX IF NOT EXISTS class_sessions_by_subject ON class_sessions(subject, started_at);
CREATE TABLE IF NOT EXISTS class_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES class_sessions(id) ON DELETE CASCADE,
    t_start REAL NOT NULL,             -- seconds from the session start
    t_end REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('room','mac')),
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS class_segments_by_session ON class_segments(session_id, t_start);
CREATE VIRTUAL TABLE IF NOT EXISTS class_segments_fts USING fts5(text, content='class_segments', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS class_segments_ai AFTER INSERT ON class_segments BEGIN
    INSERT INTO class_segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS class_segments_ad AFTER DELETE ON class_segments BEGIN
    INSERT INTO class_segments_fts(class_segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS class_actions (
    id TEXT PRIMARY KEY,               -- ca_<hex6>
    session_id TEXT NOT NULL REFERENCES class_sessions(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('homework','test','reading','bring','other')),
    due TEXT,                          -- YYYY-MM-DD, SGT
    due_text TEXT NOT NULL DEFAULT '',
    seg_id INTEGER,
    priority_id TEXT,
    done INTEGER NOT NULL DEFAULT 0
);
