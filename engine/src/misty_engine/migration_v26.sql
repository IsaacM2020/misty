-- v26 (step E, Teacher): the map of what he is learning. Nothing about any subject is in code, every row is data.
-- A topic carries the cite (source, chunk number, a short quote) that it was taken from.
CREATE TABLE IF NOT EXISTS teach_subjects (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS teach_topics (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES teach_subjects(id),
  key TEXT NOT NULL,
  title TEXT NOT NULL,
  source_id TEXT NOT NULL,
  chunk_no INTEGER NOT NULL,
  quote TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',   -- active | merged | dropped
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS teach_topics_subject ON teach_topics(subject_id, status);
CREATE TABLE IF NOT EXISTS teach_needs (
  topic_id TEXT NOT NULL REFERENCES teach_topics(id),
  needs_id TEXT NOT NULL REFERENCES teach_topics(id),
  origin TEXT NOT NULL,                    -- model | him
  status TEXT NOT NULL DEFAULT 'active',
  PRIMARY KEY (topic_id, needs_id)
);
CREATE TABLE IF NOT EXISTS teach_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id TEXT NOT NULL REFERENCES teach_topics(id),
  at REAL NOT NULL,
  kind TEXT NOT NULL,
  weight REAL NOT NULL,
  origin TEXT NOT NULL,                    -- lesson | drill | exam | class | chat
  origin_id TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS teach_evidence_topic ON teach_evidence(topic_id, at);
CREATE TABLE IF NOT EXISTS teach_slips (
  id TEXT PRIMARY KEY,
  evidence_id INTEGER REFERENCES teach_evidence(id),
  topic_id TEXT NOT NULL REFERENCES teach_topics(id),
  at REAL NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  his_answer TEXT NOT NULL DEFAULT '',
  right_answer TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',           -- stays empty until E12
  timed INTEGER NOT NULL DEFAULT 0
);
