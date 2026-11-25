-- v32 (step E, E7): the lesson that is running. The row is the whole state, so a restart changes nothing.
-- status: live | paused | done | stopped. At most one `live` row. `extra` holds {n, checks, wrong, offer, seq, parent}.
CREATE TABLE IF NOT EXISTS teach_live (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL REFERENCES teach_topics(id),
  plan_id TEXT NOT NULL REFERENCES teach_plans(id),
  step_no INTEGER NOT NULL DEFAULT 0,
  phase TEXT NOT NULL DEFAULT 'check',     -- check | guided | solo | done
  way_no INTEGER NOT NULL DEFAULT 0,
  misses INTEGER NOT NULL DEFAULT 0,
  hints_used INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'live',
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  extra TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS teach_live_one ON teach_live(status) WHERE status='live';
