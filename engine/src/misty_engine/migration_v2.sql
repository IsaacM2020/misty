-- v2: spend.purpose (Q1) and the entity registry (Q2).
ALTER TABLE spend ADD COLUMN purpose TEXT;
CREATE INDEX IF NOT EXISTS spend_purpose_ts ON spend(purpose, ts);

CREATE TABLE IF NOT EXISTS entities (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('person','project','topic','area','chat')),
  name        TEXT NOT NULL,
  name_pinned INTEGER NOT NULL DEFAULT 0,
  path        TEXT,
  status      TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','active','merged','dropped')),
  merged_into TEXT REFERENCES entities(id),
  origin      TEXT NOT NULL DEFAULT 'compile',
  area        TEXT,
  importance  INTEGER NOT NULL DEFAULT 3,
  pending     INTEGER NOT NULL DEFAULT 0,
  known       INTEGER NOT NULL DEFAULT 0,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS entities_path ON entities(path) WHERE path IS NOT NULL AND status = 'active';
CREATE INDEX IF NOT EXISTS entities_kind_status ON entities(kind, status);

CREATE TABLE IF NOT EXISTS entity_aliases (
  entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  alias      TEXT NOT NULL,
  alias_norm TEXT NOT NULL,
  kind       TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'compile',
  n          INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (entity_id, alias_norm)
);
CREATE INDEX IF NOT EXISTS aliases_lookup ON entity_aliases(kind, alias_norm);

CREATE TABLE IF NOT EXISTS entity_handles (
  type      TEXT NOT NULL CHECK (type IN ('phone','email','jid')),
  value     TEXT NOT NULL,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  PRIMARY KEY (type, value)
);

CREATE TABLE IF NOT EXISTS mentions (
  item_id   TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  raw       TEXT NOT NULL,
  role      TEXT NOT NULL DEFAULT 'named' CHECK (role IN ('named','counterpart','member')),
  PRIMARY KEY (item_id, entity_id, raw)
);
CREATE INDEX IF NOT EXISTS mentions_entity ON mentions(entity_id);

CREATE TABLE IF NOT EXISTS entity_merges (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       REAL NOT NULL,
  kept     TEXT NOT NULL,
  dropped  TEXT NOT NULL,
  reason   TEXT NOT NULL,
  verdict  TEXT,
  snapshot TEXT NOT NULL,
  undone   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS judgements (
  pair_key TEXT NOT NULL,
  version  INTEGER NOT NULL,
  verdict  TEXT NOT NULL CHECK (verdict IN ('same','different','unsure')),
  why      TEXT,
  a_n      INTEGER NOT NULL DEFAULT 0,
  b_n      INTEGER NOT NULL DEFAULT 0,
  ts       REAL NOT NULL,
  PRIMARY KEY (pair_key, version)
);

CREATE TABLE IF NOT EXISTS entity_text (
  entity_id    TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
  summary      TEXT NOT NULL DEFAULT '',
  now          TEXT NOT NULL DEFAULT '[]',
  relationship TEXT,
  role         TEXT,
  status_label TEXT,
  summary_n    INTEGER NOT NULL DEFAULT 0,
  summarised_at REAL,
  model        TEXT,
  prompt_v     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entity_redirects (
  old_path  TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entity_vec (
  entity_id TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
  vec       BLOB NOT NULL
);
