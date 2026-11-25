-- v7: chat + memory (2026-10-21). Facts are what Isaac tells Misty to remember; the chat is one thread.
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,                 -- short topic, e.g. "english task": one active fact per key
    text TEXT NOT NULL,                -- what Isaac said, as a sentence
    link TEXT,                         -- a URL or file path, when the fact points at something
    link_title TEXT,
    vec BLOB,                          -- local embedding of "key: text" (float32), for matching
    status TEXT NOT NULL DEFAULT 'active',   -- active | replaced | forgotten
    replaced_by TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_active ON facts(status, key);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,                -- user | assistant
    text TEXT NOT NULL,
    meta TEXT,                         -- JSON: what Misty did this turn (tools, links), for the app
    created_at REAL NOT NULL
);
