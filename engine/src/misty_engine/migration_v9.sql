-- v9: online vs in-person classes (2026-10-24). 'online' = the teacher comes out of the Mac (Zoom, Meet, Teams, any
-- platform), so segments with source 'mac' are the teacher and classmates and 'room' (the mic) is Isaac.
-- 'room' = in person: one mic, nobody labelled.
ALTER TABLE class_sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'room' CHECK (mode IN ('room','online'));
