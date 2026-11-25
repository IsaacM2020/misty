-- v36 (step E, E14): a course by talking. One course per subject at a time (older ones are dropped, never deleted).
-- proposed_json is the course's settings: {"skip": [topic ids], "manual_pace": bool, "over": [topic ids], "free": [dates]}.
-- teach_course_days is the plan day by day (SGT dates). A dropped course keeps its days for the record.
CREATE TABLE IF NOT EXISTS teach_courses (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES teach_subjects(id),
  goal TEXT NOT NULL DEFAULT '',
  by_date TEXT,                              -- YYYY-MM-DD, SGT
  per_day REAL,                              -- new topics a day (can be under 1)
  status TEXT NOT NULL DEFAULT 'proposed',   -- proposed | active | paused | dropped
  proposed_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS teach_courses_subject ON teach_courses(subject_id, status);
CREATE TABLE IF NOT EXISTS teach_course_days (
  course_id TEXT NOT NULL REFERENCES teach_courses(id),
  day TEXT NOT NULL,
  topic_id TEXT NOT NULL,
  kind TEXT NOT NULL,                        -- new | review
  status TEXT NOT NULL DEFAULT 'planned',    -- planned | done | missed | moved
  PRIMARY KEY (course_id, day, topic_id)
);
