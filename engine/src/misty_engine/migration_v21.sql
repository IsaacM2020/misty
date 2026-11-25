-- v21: her own timers (step D, card D2). A timer he asked for by talking; when it ends she tells him through the gate.
CREATE TABLE IF NOT EXISTS timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    ends_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',    -- active | done (it ended and was handed to the gate) | stopped (he stopped it)
    closed_at REAL
);
CREATE INDEX IF NOT EXISTS timers_status ON timers(status, ends_at);
