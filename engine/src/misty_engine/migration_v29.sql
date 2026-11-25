-- v29 (step E, E4): the one saved link from a teach subject to the subject name his classes use.
ALTER TABLE teach_subjects ADD COLUMN class_subject TEXT NOT NULL DEFAULT '';
