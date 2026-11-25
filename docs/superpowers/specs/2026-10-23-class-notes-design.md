# Class Notes design

Status: approved by Isaac (2026-10-23). The look is deliberately left out. Isaac rejected both prototype looks
(black "Living Island", then luminous white) as "AI slop", so this build uses plain SwiftUI in the current Theme and the
visual redesign comes back later as its own piece of work.

## Why
Isaac wants Granola for school. He turns it on in a real classroom or an online class, sees the live transcript, types
rough notes if he wants, and after class gets complete notes with action items. It is not a separate notes app. It is
wired into the rest of Misty: calendar, Priorities, suggestions, chat and voice. His words: "it has to be a whole
integrated system".

## What he decided (in his words where possible)
- Works for in-person classes and online classes (Zoom, Meet, Teams).
- During class, Misty is a **side panel** next to whatever he is doing.
- **Real-time transcript**, plus a **Transcript tab** he can scroll back through.
- **Ask the class**: questions like "what did he say about Le Chatelier?", during class or after.
- **Fill in my notes = a whole set of notes**: a summary, the key points, everything done, "even if I didn't write
  anything in the notes section". His own typed notes are folded in.
- **Action items at the end**: homework, deadlines and the like.
- Action items **go onto Priorities** with their due dates.
- Misty **blocks time on his calendar** to do the homework ("Yes, it should do it").
- Privacy rule from earlier sessions: Misty never keeps or analyses room **recordings**. Audio is never written to
  disk. Only the text transcript and the notes are kept.

## Goals
1. One tap or "Misty, class mode" starts a class session; the transcript shows within about a second of each phrase.
2. After End, complete notes within about 30 s for a 60 minute class.
3. Every key point and action item links back to the moment in the transcript it came from.
4. Action items show up in Priorities and on his calendar without him doing anything.
5. Any past class is searchable and askable from Class Notes, chat and voice.

## Non-goals (this build)
- The visual redesign, sounds and playful animation. Plain and clear now, beautiful later.
- Telling speakers apart by voice (teacher vs classmate). The only split is "this Mac" (online audio) vs "the room" (mic).
- Capturing slides or the screen.
- Sharing or exporting notes to anyone. Copy as Markdown is enough.
- iPhone or iPad.

## Anti-goals (a failure even if it works)
- Audio saved anywhere, or sent to the cloud. Speech to text stays on the Mac (Phonon-2 on the Neural Engine).
- Misty speaking or acting during class. Class mode stays silent, the same as today.
- Recording that starts on its own. Calendar matching only offers. Isaac always starts it himself.
- Made-up content: a key point or action item with no transcript moment behind it is dropped, not shown.
- Notes that bury his own lines. What he typed stays his, word for word, and is marked as his.

## How it works

### 1. Starting a class
- **From the calendar:** the gcal source already reads his week. An event whose title matches a subject (Chem,
  Chemistry, Math, Math AA, Physics, Econ, Economics, English, Spanish, plus aliases he can edit) counts as a class.
  Two minutes before it starts, Misty shows "Chem starts at 10:00. Take notes?" with Start / Not now. Showing the offer
  also starts warming the ears, because the Neural Engine warm-up takes about 65 s per launch.
- **By hand:** Start class in the menu bar or the main window, or say "Misty, class mode" in live mode. He picks the
  subject, and it defaults to the calendar match when there is one.
- **The session** gets a subject, a title (the event title or "Chem, 23 Oct"), a start time and the calendar event id
  if there is one.

### 2. Hearing the class
- **The room (mic):** the existing `AudioIO` + `Ears` (Phonon-2 Core ML). For a class the Endpointer cuts phrases as it
  does now, and each final phrase becomes one transcript segment `{t_start, t_end, text, source: "room"}`. Partials
  show live in the panel and are not saved.
- **Online classes (this Mac's sound):** new. ScreenCaptureKit audio-only capture (`SCStream` with `capturesAudio`, no
  video frames kept). It needs the Screen Recording permission, which Misty already asks for to look at windows. This
  audio goes through a second `Ears` reader, giving `source: "mac"` segments. Both streams are on by default, so a Zoom
  class with him speaking into the mic gets both sides.
- **Echo:** with speakers on, the mic also hears Zoom. Voice processing on the mic cancels most of it. Any room segment
  that mostly repeats a mac segment within ±2 s is dropped. With headphones the problem doesn't come up.
- **Long sessions:** Ears was built for short turns. A class session runs it for 40 to 90 minutes, reading partials
  only every 0.8 s (the live-mode listening rate). It keeps no audio beyond the phrase in flight.
- **Live mode** and Class Notes share the mic. While a class runs, live mode is in class mode (silent) and its usual
  to-do hearing is off, because Class Notes action items replace it.

### 3. During class (side panel)
- A floating panel about 380 pt wide that stays on top of Zoom or his notes. It can be moved, and collapsed to a small
  pill showing the timer and a live dot.
- **Notes tab** (default): a plain text editor, Markdown-ish (bullets, headings). It saves as he types. A small timer
  sits on each line he writes (the transcript time when he started the line), so his notes can be matched to the
  class later.
- **Transcript tab:** live segments with times. The newest phrase is grey until it is final. Auto-scroll stops when he
  scrolls up, and a "Jump to now" button brings it back.
- **Ask box** at the bottom of both tabs. It answers from the transcript so far (section 5).
- **Pause / End.** Pause stops both ears. End closes the session and starts the notes.

### 4. After class: the notes
End runs one engine job, `class_notes`, which makes one cloud LLM call through the existing OpenRouter path and spend
caps. It is sent the subject, title and date, the whole transcript as numbered segments, and his typed notes with
their timers. It returns JSON:

```
summary:        3-5 sentences, what the class covered
sections:       [{heading, points: [{text, seg: [ids]}]}]   key points grouped by topic
his_notes:      his lines placed into the sections they belong to, word for word, flagged mine
remember:       [{kind: definition|formula|exam_hint, text, seg}]   "this will be on the test" goes here
action_items:   [{text, kind: homework|test|reading|bring|other, due: date|null, due_text, seg}]
```

- The engine checks every `seg` id. A point or action item with no valid segment is dropped (anti-goal: nothing made
  up). Due dates like "next Tuesday" are resolved from the class date in SGT. If the date is unclear, `due` is null and
  `due_text` keeps what the teacher said.
- If he typed nothing, the same call simply has no `his_notes`. It always produces full notes.
- The notes view shows: Summary, then the sections (his lines in his normal text, Misty's in a lighter style), then
  Remember, then Action items. Every item has a time chip that opens the Transcript tab at that moment.
- **Regenerate** reruns the job, for example after he adds notes. **Edit:** the notes are editable text once made, and
  edits are saved as his.
- **Cost:** a 60 minute class is roughly 9,000 words, about 12k tokens in and 1.5k out. On the chat model that is
  about $0.01 to $0.03 per class. It counts toward the existing daily cap under a new purpose, `class`.

### 5. Ask the class
- **Search index:** transcript segments and the finished notes go into a SQLite FTS5 table, and also into the existing
  embedding index (`embed.py`) once the class ends.
- **One class** (from its panel or notes): retrieve the top segments for the question (FTS plus embeddings, ±1
  neighbouring segment for context), then one LLM call. The answer comes with time chips. During class it searches what
  has been said so far.
- **Across classes:** the same, filtered by subject and/or date ("everything on equilibrium in Chem this month").
- **Chat and voice:** two new chat tools, `class_search(query, subject?, since?)` and `class_notes(session_id | "last"
  [, subject])`. "What did we do in Physics today?" and "when's the Spanish oral due?" use these. Voice uses the same
  tools.

### 6. Priorities
- Each action item with a real task (homework, reading, test prep, bring) is added to `priorities.md` automatically.
  The line format grows two optional fields: `<!-- id:p3a9f area:chem due:2026-10-28 class:cs_ab12 -->`.
- These lines skip the draft/approve step that the weekly priorities rewrite uses, because Isaac asked for them to be
  added directly. The weekly rewrite keeps class lines unless they are done or more than 7 days overdue.
- Done: ticking a line in Priorities also ticks it in the class notes, and the other way round.
- The Priorities view shows the due date and a link to the class.

### 7. Calendar blocks
- For each homework, reading or test item with a due date, Misty puts a time block on his **primary Google
  calendar**: "Chem homework: Q1-8 p.112 (Misty)". It goes in the first free slot that fits before the due date: after
  school (16:00-21:30 SGT) on a school day, any time 10:00-21:30 on weekends, and never on Sunday mornings (church).
  Lengths: homework 45 min, reading 30, test prep 2×45 on two different days.
- **Never** guests, invites or emails. Only his own calendar. Every block is tagged in its private extended properties
  (`misty_class=<session>`, `misty_item=<id>`), so Misty only ever moves or deletes blocks she made.
- He can drag a block in Google Calendar and Misty leaves it there. Undo: "Remove block" on the action item deletes
  that one event. Settings has a switch to turn auto-blocking off.
- **What this needs:** Misty's Google sign-in is read-only today (`google_auth.SCOPES`). Adding
  `https://www.googleapis.com/auth/calendar.events` means **one new Google sign-in by Isaac**. Until he does it, action
  items still go to Priorities and the notes show "Calendar blocks need Google access" with a button.

### 8. Suggestions
- **Test coming:** for a `test` action item, a suggestion card the evening before: "Chem test tomorrow. 3 classes
  covered it" opens a combined Remember list from those classes. Uses the existing `suggestions` table, kind `task`.
- **Homework not started:** if a homework item is due tomorrow and not ticked, a card at 18:00.
- **Missed a class session:** if a calendar class passed with no session, nothing happens. No guilt cards.

### 9. Storage (engine, new migration)
- `class_sessions(id, subject, title, cal_event_id, started_at, ended_at, status: live|paused|ended|noted, his_notes,
  notes_json, cost)`
- `class_segments(id, session_id, t_start, t_end, source: room|mac, text)` + FTS5 `class_segments_fts`
- `class_actions(id, session_id, text, kind, due, due_text, seg_id, priority_id, cal_event_id, done)`
- Audio: none. Segment text goes through `scrub_text` before it is saved, the same as other text.
- Transcripts are kept until he deletes the class. Deleting a class removes its segments, notes, priority lines and
  the calendar blocks Misty made.

### 10. App screens (functional, current Theme)
- **Class panel** (floating, section 3).
- **Classes** tab in the main window: a list of classes grouped by subject, newest first, with search at the top. It
  opens a class with Notes / Transcript tabs and an Ask box.
- Settings, Class Notes section: subject names and aliases, sources on/off (room, this Mac), auto calendar blocks on/off,
  the Google access button.

### 11. RPC (engine ↔ app)
`class.start {subject, title?, cal_event_id?}`, `class.segment {session, t_start, t_end, source, text}` (app → engine,
per final phrase), `class.notes.save {session, text}`, `class.pause`, `class.end`, `class.get`, `class.list`,
`class.ask {session? | subject?, question}`, `class.regenerate`, `class.delete`, `class.action.done`, plus a
`class.upcoming` notification for the calendar offer.

## Calls I made (Isaac, check these)
- Calendar matching **offers** and never auto-starts recording.
- Both audio sources on by default; duplicate echo phrases dropped.
- Calendar blocks are automatic (your yes), own calendar only, never guests, with set lengths and after-school
  windows, never Sunday mornings.
- Class action items skip the Priorities approve step.
- Notes come from one cloud call after class (about 1 to 3 cents), not running live during class.
- Transcripts kept until you delete the class.
- Plain look for now.

## Risks
- **School rules:** recording a class, even as text, may need the teacher's OK. That is Isaac's call per class.
  Misty shows a small "transcribing" dot the whole time so it is never hidden.
- **Neural Engine warm-up (~65 s):** the calendar offer pre-warms the ears. A hand start in the first minute after
  launch shows "Ears warming up" and keeps the panel usable for typing.
- **Ears over 90 minutes:** untested at that length. The first build includes a 60 minute soak test with a public lecture
  audio file (played once and not kept) to check memory and drift.
- **The always-on voice-processing mic may clash with Ripple2.** Same open risk as live mode.

## Build order
1. Engine: migration, `class.*` RPC, segment saving + FTS, the notes job with segment checking and due-date
   resolution, tests.
2. App: class session controller (mic ears + segments), side panel (Notes / Transcript / Ask), End → notes view.
3. Ask (one class, then across classes) + chat/voice tools.
4. Priorities lines + the two-way done sync.
5. This Mac's audio via ScreenCaptureKit + echo drop.
6. Calendar: offer from the gcal source, then calendar blocks after the new Google sign-in.
7. Suggestions (test coming, homework not started).

## Done when
- Engine tests pass (`cd engine && .venv/bin/python -m pytest ../tests/engine -q`), and the Swift tests pass
  (`bash scripts/swift_test.sh`).
- A recorded lecture played through the speakers gives a live transcript, and End gives notes with valid time chips and
  action items.
- Action items appear in Priorities with due dates, and as blocks on his calendar once Google access is granted.
- "What did we do in Chem today?" in chat answers from the class.
- Isaac uses it in one real class and the notes are worth keeping.

## Changes after review (builder check + Fo, 2026-10-23)
These override the sections above where they differ.
- **This Mac's audio is a spike, not a given.** ScreenCaptureKit hung inside Misty on 2026-10-21 (`Capture.swift`,
  HANDS_TIMEOUT), and that is why hands use the `screencapture` CLI. Step 5 starts with a time-boxed spike: audio-only
  `SCStream` in a separate helper process, so a hang can't freeze Misty. If it hangs there too, online classes fall
  back to the mic hearing the Mac's speakers, with a "use speakers, not headphones" hint. No audio drivers get
  installed. (My call.)
- **Done lives in `class_actions.done`.** Priorities gets real checkboxes for class lines only. `priorities.py` `Line`
  gains `due`, `cls` and `done`, `_render` writes them back, and the weekly diff matches class lines by id, not text.
  Class lines live in their own `## From class` section, so the weekly draft/approve flow never touches them. (My call.)
- **Calendar write access uses its own token.** A second Keychain entry is loaded only to create, move or delete
  blocks. The existing read-only token is never re-scoped, so Gmail and calendar reads can't break. Check whether the
  OAuth app is in Testing mode (refresh tokens expire after 7 days) before building step 6. (My call.)
- **Event-level calendar read.** `gcal.py` only makes week-text items, so the class offer needs a new small read of
  today's events (id, start, end, title) every 15 min. Subject matching uses an alias list in Settings, seeded from his
  real timetable titles.
- **Suggestions:** each class session gets an `items` row (kind `class_session`), so cards dedupe through the existing
  `UNIQUE(item_id, kind)`. (My call.)
- **Scrubbing:** `scrub_text` targets secrets (keys, tokens, passwords), not names or page numbers. Notes are made from
  the scrubbed text. Step 1 adds a test that a homework line like "Q1-8 page 112, due 28 Oct" survives the scrub. (My call.)
- **Room audio in a real classroom:** a teacher across the room isn't close talk. Class sessions get their own Ears
  settings (AGC on, a lower minimum level, no 15 s phrase cap, cuts on 0.6 s pauses or 20 s max). The first real test
  is one actual class, before any promise that notes from an empty notebook will be good. (Fo + builder.)
- **End-of-day review card (Fo):** at 21:00, one card lists today's new class action items. Each has Keep / Fix /
  Remove, so a misheard homework line gets caught before it is scheduled.
- **Auto-end:** a session ends on its own 10 min after its calendar event ends, or after 15 min of no speech, with a
  notification.
- **Failures:** if the notes call fails, the session stays `ended`, the job retries 3 times with backoff, and the notes
  view shows "Notes not made yet, Retry". If the transcript is under ~150 words or mostly junk, there are no notes:
  "Not enough was heard to make notes."
- **Thermals:** log Neural Engine time and thermal state each minute in a class. If macOS reports `serious`, partials
  slow to every 2 s.
- **Wiki recall (Fo):** each class's Remember list and summary is written to the subject's wiki page, so chat and the
  weekly priorities already know about it.

## Isaac's answers (2026-10-23, approved)
- **Timetable:** not on Google Calendar yet. Isaac will have Fo add it. Until then, classes are started by hand; the
  alias list gets seeded from the real titles once they are in.
- **Laptop in class:** on his desk with the lid open, or in his bag with the lid closed, depending on the teacher. A
  MacBook on battery sleeps when the lid closes, and the mic stops with it. Misty can't stop that without changing
  system sleep settings, which it must not do. So a lid-closed class is not transcribed. When the lid closes during a
  session, the session pauses with a mark in the transcript ("lid closed 10:14-10:52"), and the notes say which part was
  missed. Hearing a lid-closed class needs another device later (iPhone), which stays out of scope.
- **Calendar blocks: ask first.** This replaces "automatic" in section 7. Each evening, with the 21:00 review card,
  Misty proposes the blocks for the new items. One tap accepts them all, and each can be moved or dropped first. Nothing
  goes on the calendar without that tap.
- **Retention:** transcripts deleted 6 months after the class (a daily job). Notes, action items and the Remember lists
  are kept forever. "Ask the class" on an older class answers from its notes only and says the transcript is gone.
