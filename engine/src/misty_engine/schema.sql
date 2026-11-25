CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS items (
  id            TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  kind          TEXT NOT NULL,
  locator       TEXT NOT NULL,
  fingerprint   TEXT,
  title         TEXT NOT NULL DEFAULT '',
  created_at    TEXT,
  modified_at   TEXT,
  status        TEXT NOT NULL DEFAULT 'found' CHECK (status IN
                ('found','scrubbed','triaged','compiled','embedded','linked','done','skipped','compile_failed')),
  matters       INTEGER,
  area          TEXT,
  note_path     TEXT,
  word_count    INTEGER,
  secret_file   INTEGER NOT NULL DEFAULT 0,
  meta          TEXT NOT NULL DEFAULT '{}',
  error_code    TEXT,
  updated_at    REAL NOT NULL,
  UNIQUE (source, locator)
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE INDEX IF NOT EXISTS items_source_mod ON items(source, modified_at);

CREATE TABLE IF NOT EXISTS item_chunks (
  item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  chunk_no      INTEGER NOT NULL,
  text_scrubbed TEXT NOT NULL,
  PRIMARY KEY (item_id, chunk_no)
);

-- ref_kind: 'chunk' (ref_id = '<item_id>:<chunk_no>') | 'page' (ref_id = wiki-relative path)
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  text, ref_kind UNINDEXED, ref_id UNINDEXED, tokenize = 'porter unicode61 remove_diacritics 2'
);

-- maps vectors.rowid -> what it is. ref_kind: 'chunk' | 'page' | 'image' | 'item'
CREATE TABLE IF NOT EXISTS vec_refs (
  rowid    INTEGER PRIMARY KEY,
  ref_kind TEXT NOT NULL,
  ref_id   TEXT NOT NULL,
  item_id  TEXT,
  source   TEXT,
  UNIQUE (ref_kind, ref_id)
);

CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  item_id     TEXT,
  payload     TEXT NOT NULL DEFAULT '{}',
  cloud       INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
              ('queued','running','waiting_network','waiting_cap','waiting_quota','failed','done')),
  attempts    INTEGER NOT NULL DEFAULT 0,
  next_run_at REAL NOT NULL,
  error_code  TEXT,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active ON jobs(kind, item_id)
  WHERE item_id IS NOT NULL AND status NOT IN ('done','failed');
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, next_run_at);

CREATE TABLE IF NOT EXISTS suggestions (
  id           TEXT PRIMARY KEY,
  item_id      TEXT REFERENCES items(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('reply','calendar','task','fact','mute')),
  text         TEXT NOT NULL,
  urgency      TEXT NOT NULL DEFAULT 'none' CHECK (urgency IN ('now','today','this_week','none')),
  status       TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','done','snoozed','dismissed')),
  snooze_until REAL,
  sender       TEXT,
  created_at   REAL NOT NULL,
  updated_at   REAL NOT NULL,
  UNIQUE (item_id, kind)
);

CREATE TABLE IF NOT EXISTS spend (
  id       INTEGER PRIMARY KEY,
  ts       REAL NOT NULL,
  provider TEXT NOT NULL,
  model    TEXT NOT NULL,
  job_id   TEXT,
  build_id TEXT,
  usd      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS spend_ts ON spend(ts);

CREATE TABLE IF NOT EXISTS builds (
  id          TEXT PRIMARY KEY,
  started_at  REAL NOT NULL,
  finished_at REAL,
  item_limit  INTEGER,
  phase       TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('running','paused','done','failed'))
);

CREATE TABLE IF NOT EXISTS sources_state (
  source     TEXT PRIMARY KEY,
  cursor     TEXT,
  health     TEXT NOT NULL DEFAULT 'ok' CHECK (health IN
             ('ok','paused','error','needs_auth','needs_fda','needs_export','disabled')),
  last_ok_at REAL,
  error_code TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
