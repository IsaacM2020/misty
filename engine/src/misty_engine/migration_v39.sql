-- v39 (step E, E13): paper mode. A past paper and its mark scheme, parsed once, then timed runs marked against the scheme.
-- teach_papers.questions_json = {"questions": [{no, text, marks, cite, points: [{text, quote, chunk_no}]}], "failed": [no, ...]}.
-- status of a paper: new | ready | failed. status of a run: live | done | stopped | timeout. One run is live at a time.
-- answers_json = {question no: his answer}. marks_json = {question no: {got, of, covered: [point text], missed: [point text], verdict}}.
CREATE TABLE IF NOT EXISTS teach_papers (
  id TEXT PRIMARY KEY,
  subject_id TEXT REFERENCES teach_subjects(id),
  paper_source_id TEXT NOT NULL,
  scheme_source_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new',
  questions_json TEXT NOT NULL DEFAULT '',
  made_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS teach_papers_pair ON teach_papers(paper_source_id, scheme_source_id);
CREATE TABLE IF NOT EXISTS teach_paper_runs (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES teach_papers(id),
  status TEXT NOT NULL DEFAULT 'live',
  minutes INTEGER NOT NULL,
  started_at REAL NOT NULL,
  ends_at REAL NOT NULL,
  answers_json TEXT NOT NULL DEFAULT '{}',
  marks_json TEXT NOT NULL DEFAULT '{}',
  total REAL NOT NULL DEFAULT 0,
  out_of INTEGER NOT NULL DEFAULT 0,
  estimate_json TEXT NOT NULL DEFAULT '',
  extra TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS teach_paper_runs_one ON teach_paper_runs(status) WHERE status='live';
