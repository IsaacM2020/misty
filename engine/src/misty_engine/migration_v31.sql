-- v31 (step E, E5): one lesson plan per topic and version. plan_json holds the checked plan (cites real, keys passed E6, boards drawn).
-- status: draft (being made) | usable | rejected (why says what failed). A rejected plan is kept as a record and never repaired.
CREATE TABLE IF NOT EXISTS teach_plans (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL REFERENCES teach_topics(id),
  version INTEGER NOT NULL,
  plan_json TEXT NOT NULL DEFAULT '{}',
  model TEXT NOT NULL DEFAULT '',
  cost REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  why TEXT NOT NULL DEFAULT '',
  made_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS teach_plans_topic ON teach_plans(topic_id, status, version);
