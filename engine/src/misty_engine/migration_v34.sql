-- v34 (step E, E11): the drill that is running. The row is the whole state, so a restart changes nothing.
-- kind: drill | probe (E17) | review (E12). status: live | paused | done | stopped. At most one `live` row.
-- qids is a json list of teach_questions ids; i is the question being asked. extra holds {partly, seq}.
CREATE TABLE IF NOT EXISTS teach_drill (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL DEFAULT '',
  topic_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'drill',
  status TEXT NOT NULL DEFAULT 'live',
  qids TEXT NOT NULL DEFAULT '[]',
  i INTEGER NOT NULL DEFAULT 0,
  right INTEGER NOT NULL DEFAULT 0,
  wrong INTEGER NOT NULL DEFAULT 0,
  extra TEXT NOT NULL DEFAULT '{}',
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS teach_drill_one ON teach_drill(status) WHERE status='live';
