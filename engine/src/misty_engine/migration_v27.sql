-- v27 (step E, Teacher, E2): what he can be taught from. One row per source. No text is copied where it already exists:
-- a file already in the index points at its item, a class points at its class session. Only pages and files outside
-- his indexed folders keep their (scrubbed) text in teach_chunks. Nothing here touches items or vec_refs, so the
-- memory pipeline never compiles a teaching source into the wiki.
CREATE TABLE IF NOT EXISTS teach_sources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                      -- class | file | page | syllabus
  title TEXT NOT NULL,
  locator TEXT NOT NULL,                   -- class id, resolved path, or url without fragment
  item_id TEXT,                            -- set when the text already lives in item_chunks
  class_id TEXT,                           -- set for a class
  chars INTEGER NOT NULL DEFAULT 0,
  added_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'    -- active | dropped
);
CREATE INDEX IF NOT EXISTS teach_sources_locator ON teach_sources(locator, status);
CREATE TABLE IF NOT EXISTS teach_chunks (
  source_id TEXT NOT NULL REFERENCES teach_sources(id),
  chunk_no INTEGER NOT NULL,
  text_scrubbed TEXT NOT NULL,
  PRIMARY KEY (source_id, chunk_no)
);
CREATE VIRTUAL TABLE IF NOT EXISTS teach_chunks_fts USING fts5(text_scrubbed, content='teach_chunks', content_rowid='rowid',
  tokenize = 'porter unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS teach_chunks_ai AFTER INSERT ON teach_chunks BEGIN
  INSERT INTO teach_chunks_fts(rowid, text_scrubbed) VALUES (new.rowid, new.text_scrubbed);
END;
CREATE TRIGGER IF NOT EXISTS teach_chunks_ad AFTER DELETE ON teach_chunks BEGIN
  INSERT INTO teach_chunks_fts(teach_chunks_fts, rowid, text_scrubbed) VALUES ('delete', old.rowid, old.text_scrubbed);
END;
