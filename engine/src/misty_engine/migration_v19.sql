-- v19: the activity trail (step C, card C7). One row per stretch in one app and document. Local only, kept 30 days, private
-- apps never saved, can be paused by talking (setting activity_trail). No screen text, only the app, window title and doc link.
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    until REAL NOT NULL,
    bundle TEXT NOT NULL,
    app TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    doc TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS activity_at ON activity(at);
