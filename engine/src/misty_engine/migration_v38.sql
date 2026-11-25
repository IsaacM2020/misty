-- v38 (step E, E16): which teaching ways a lesson plan was made under. A plan is cached per topic, so when he changes how he wants
-- to be taught, a plan made under other ways must not be used. '' = made with no teach ways.
ALTER TABLE teach_plans ADD COLUMN ways_hash TEXT NOT NULL DEFAULT '';
