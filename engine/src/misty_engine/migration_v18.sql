-- v18: the wrong-pick log (step B, card B2b). When he corrects a wrong tool pick ("no, I meant the drill"), the phrase he
-- said before and the tool she picked are kept here, so they can join his tool-pick set on his yes. Stays on this Mac.
CREATE TABLE IF NOT EXISTS wrong_picks (
    id TEXT PRIMARY KEY,
    said TEXT NOT NULL,                -- what he said on the turn she got wrong
    picked_tool TEXT NOT NULL,         -- the tool she called ('none' = she just answered)
    picked_args TEXT NOT NULL DEFAULT '',   -- its arguments, short
    wanted_tool TEXT NOT NULL,         -- the tool she should have called ('none' = she should have just answered)
    note TEXT NOT NULL DEFAULT '',     -- his correction in his words
    status TEXT NOT NULL DEFAULT 'new',     -- new | added | dismissed
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS wrong_picks_status ON wrong_picks(status, created_at);
