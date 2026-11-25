-- v24: routines (step D, card D9). A protocol may also start from a time or an event, through the gate.
ALTER TABLE protocols ADD COLUMN when_spec TEXT NOT NULL DEFAULT '';     -- JSON {kind: time|first_open|class_end|timer_end, ...} or ''
ALTER TABLE protocols ADD COLUMN auto INTEGER NOT NULL DEFAULT 0;        -- 1 = runs without asking (only when every step is safe)
-- One row per trigger that was dealt with, so a routine fires once per event: told | ran | dropped (waited too long).
CREATE TABLE IF NOT EXISTS routine_fires (
    key TEXT PRIMARY KEY,
    protocol TEXT NOT NULL,
    at REAL NOT NULL,
    outcome TEXT NOT NULL
);
