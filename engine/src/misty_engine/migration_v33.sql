-- v33 (step E, E10): the question bank. Extra questions per topic, each with a key that passed E6 and a cite that is a real quote.
-- level: 1 recall, 2 apply, 3 stretch. source: bank (made by teach/bank.py) | plan (copied from a lesson plan, used by drills at most once a week).
-- status: usable | bad (E12 flagged it, never picked) | retired. uses and last_used drive "least used first".
CREATE TABLE IF NOT EXISTS teach_questions (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL REFERENCES teach_topics(id),
  level INTEGER NOT NULL,
  q TEXT NOT NULL,
  key_json TEXT NOT NULL DEFAULT '{}',
  cite_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'bank',
  status TEXT NOT NULL DEFAULT 'usable',
  made_at REAL NOT NULL,
  uses INTEGER NOT NULL DEFAULT 0,
  last_used REAL
);
CREATE INDEX IF NOT EXISTS teach_questions_topic ON teach_questions(topic_id, level, status);
