-- v6 (v3 to v5 were the dropped hands-v2 branch and already ran on the live database): suggestion prep (2026-10-21). Each card gets a plan made in the background so tapping Go just does it.
ALTER TABLE suggestions ADD COLUMN prep TEXT;        -- the plan as JSON
ALTER TABLE suggestions ADD COLUMN prep_state TEXT;  -- NULL not planned yet | ready | none | running | done | failed
ALTER TABLE suggestions ADD COLUMN prep_note TEXT;   -- one line for the card: what Go did, or why it could not
