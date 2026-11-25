-- v35 (step E, E12): the mistakes book. A slip keeps the question's key and cite so it can be asked again word for word.
-- qid is the teach_questions row it came from (a lesson slip finds or makes one when it is first reviewed). rights counts right answers
-- in a row in a review (two retires it). flagged = he said the key was wrong. Old slips have no key and are never re-asked.
ALTER TABLE teach_slips ADD COLUMN key_json TEXT NOT NULL DEFAULT '';
ALTER TABLE teach_slips ADD COLUMN cite_json TEXT NOT NULL DEFAULT '';
ALTER TABLE teach_slips ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0;
ALTER TABLE teach_slips ADD COLUMN reviewed_at REAL;
ALTER TABLE teach_slips ADD COLUMN qid TEXT NOT NULL DEFAULT '';
ALTER TABLE teach_slips ADD COLUMN rights INTEGER NOT NULL DEFAULT 0;
-- a pattern is told once per 28 days
CREATE TABLE IF NOT EXISTS teach_patterns_said (
  key TEXT PRIMARY KEY,
  at REAL NOT NULL
);
