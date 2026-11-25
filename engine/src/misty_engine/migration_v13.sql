-- v13: the gate (Season 3, Day 5). `held` is what she wants to say but is waiting for a good moment to say;
-- `told` is everything she actually said or showed on her own (the "Told you" log, Day 6 adds the feedback).
CREATE TABLE IF NOT EXISTS held (
    id TEXT PRIMARY KEY,
    at REAL NOT NULL,
    expires_at REAL NOT NULL,
    urgency TEXT NOT NULL,             -- now | soon | later
    key TEXT NOT NULL,                 -- what it is about, so the same thing is never said twice
    topic TEXT NOT NULL,               -- which follow (or other source) it came from, for back-off
    topic_text TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    source_name TEXT,
    url TEXT,
    status TEXT NOT NULL DEFAULT 'waiting',   -- waiting | delivered | expired
    why TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS held_waiting ON held(status, at);

CREATE TABLE IF NOT EXISTS told (
    id TEXT PRIMARY KEY,
    at REAL NOT NULL,
    topic TEXT NOT NULL,
    topic_text TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    text TEXT NOT NULL,                -- what the source said (for an ask, what she will say if he says yes)
    said TEXT NOT NULL,                -- the exact words shown or spoken
    source_name TEXT,
    url TEXT,
    how TEXT NOT NULL,                 -- say | card | ask
    urgency TEXT NOT NULL,
    why TEXT NOT NULL DEFAULT '',
    waited_s REAL NOT NULL DEFAULT 0,
    feedback TEXT,                     -- up | down | ignored | accepted | declined | ... (Day 6)
    feedback_at REAL
);
CREATE INDEX IF NOT EXISTS told_by_at ON told(at);
CREATE INDEX IF NOT EXISTS told_by_topic ON told(topic, at);
