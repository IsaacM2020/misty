-- v37 (step E, E15): what the daily bite has done, one row per thing said or decided, so each is said once.
-- key: bite:<course>:<day> (the day's ask), bite:<course>:<day>:eve (the evening one), classmatch:<class session> (which topic its next step
-- points at, so the matcher runs once per class), class:<class session>:<topic> (a class jump said once), replan:<course>:<day>.
-- outcome: told | accepted | later | skipped | dropped | said | none | <topic id> for classmatch.
CREATE TABLE IF NOT EXISTS teach_bite (
  key TEXT PRIMARY KEY,
  at REAL NOT NULL,
  course_id TEXT NOT NULL DEFAULT '',
  day TEXT NOT NULL DEFAULT '',
  topic_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',          -- lesson | drill | eve | class | replan | match
  outcome TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS teach_bite_course ON teach_bite(course_id, day);
