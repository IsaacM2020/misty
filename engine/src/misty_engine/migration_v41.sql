-- v41 (F6): the words the mic heard, kept on this Mac for 14 days so he can label some of them "for her" or "not".
-- Off by default (setting collect_phrases). Scrubbed text only. Never written in class mode or while a private app is in front.
CREATE TABLE IF NOT EXISTS heard_phrases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  text TEXT NOT NULL,
  state TEXT NOT NULL,                       -- listening | speaking
  named INTEGER NOT NULL DEFAULT 0,          -- her name was in it
  followup INTEGER NOT NULL DEFAULT 0,       -- the follow-up window was open
  since_reply REAL,                          -- seconds since her last reply, null when none
  outcome TEXT NOT NULL,                     -- delivered | dropped | merged | held
  label TEXT,                                -- null | for_her | not_for_her
  labelled_at REAL
);
CREATE INDEX IF NOT EXISTS heard_phrases_at ON heard_phrases(at);
CREATE INDEX IF NOT EXISTS heard_phrases_label ON heard_phrases(label, outcome);
