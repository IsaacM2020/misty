-- v40 (step E, E17): before and after. One row per probe set asked (or skipped) on a topic.
-- set_name: before | after | week. status: done | skipped | offered | left.
--   offered = the quick check was offered and he has not answered; left = he said not now, so it waits for the next drill (after only).
-- qids is a json list of 3 teach_questions ids, one per level 1 to 3, all source 'bank'. score is right answers; out_of is how many were asked.
CREATE TABLE IF NOT EXISTS teach_probes (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL,
  set_name TEXT NOT NULL,
  qids TEXT NOT NULL DEFAULT '[]',
  score INTEGER NOT NULL DEFAULT 0,
  out_of INTEGER NOT NULL DEFAULT 0,
  lesson_id TEXT NOT NULL DEFAULT '',
  at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'done'
);
CREATE INDEX IF NOT EXISTS teach_probes_topic ON teach_probes(topic_id, set_name, status);
