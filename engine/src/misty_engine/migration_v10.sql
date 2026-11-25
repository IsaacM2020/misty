-- What was on the shared screen in an online class: text read on this Mac (OCR), never the picture.
CREATE TABLE IF NOT EXISTS class_slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES class_sessions(id) ON DELETE CASCADE,
    t REAL NOT NULL,                   -- seconds from the session start
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS class_slides_by_session ON class_slides(session_id, t);
