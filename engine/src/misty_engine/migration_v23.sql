-- v23: which protocol run a move belonged to (step D, card D7). '' = he asked for the move directly.
ALTER TABLE moves_log ADD COLUMN protocol TEXT NOT NULL DEFAULT '';
