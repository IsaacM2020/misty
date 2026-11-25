-- v25: a class that was split in two (the app restarted mid-class) is merged into the first half. The other half is
-- never deleted: it stays, with merged_into pointing at the class that now holds its transcript, and the list hides it.
ALTER TABLE class_sessions ADD COLUMN merged_into TEXT;
