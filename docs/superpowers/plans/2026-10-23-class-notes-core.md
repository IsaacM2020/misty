# Class Notes (core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isaac starts a class by hand or by voice, sees the live transcript in a floating side panel, can type notes and ask questions, and on End gets full notes. Action items go onto Priorities with due dates, and chat and voice can answer from any class.

**Architecture:** The Python engine owns storage (migration v8: sessions, segments + FTS5, actions), the notes call, Ask, retention and the Priorities lines, all through `class.*` JSON-RPC. The Swift app reuses VoiceVM's one mic and Ears. In class mode, VoiceVM routes each final phrase to a new `ClassVM`, which sends `class.segment` and drives the panel and the Classes tab.

**Tech Stack:** Python 3 + SQLite FTS5 (engine), OpenRouter through `ctx.runner`, SwiftUI + AppKit `NSPanel` (app), Phonon-2 Core ML ears (existing).

**Spec:** `docs/superpowers/specs/2026-10-23-class-notes-design.md`. Read all of it. "Changes after review" and "Isaac's answers" override earlier sections.

**Scope split (spec build steps):** this plan covers steps 1 to 4: engine, panel, Ask, chat tools, Priorities, plus lid-close pause, auto-end, retention and wiki recall. Two later plans follow:
- **Plan 2:** this Mac's audio. A ScreenCaptureKit spike in a helper process, then echo drop.
- **Plan 3:** calendar. The class offer from today's events, calendar blocks proposed at 21:00 for one-tap accept on a separate write token, the 21:00 review card, and the test and homework cards.

Until Plan 3 ships, action items go to Priorities only.

## Global Constraints
- Audio is never written to disk or sent anywhere. Only segment text is kept, and it goes through `scrub_text(text, "message").text` before it is saved.
- Class mode never speaks and never acts. VoiceVM already returns early in class mode, so keep that.
- A session never starts on its own. It starts only from Start class or "Misty, class mode".
- A key point, remember item or action item whose `seg` ids aren't all real segments of that session is dropped.
- His typed lines appear word for word and are flagged `mine`.
- Paid calls: `purpose="class"`, wrapped in `openrouter.user_approved()`, through `ctx.runner.run(...)`. They are under the existing daily cap. The background-work pause (`settings.paused`) must not block them, so never send them through `JobQueue`.
- Retention: segments are deleted 183 days after `started_at`. Sessions, notes and actions are kept forever.
- Dates are resolved in SGT (`clock.sgt_date`).
- Ears encoder limit: the phrase max stays 15 s (`Endpointer.maxSeconds`, "the ears' one encoder size"). This corrects the spec's "no 15 s cap". Continuous speech is split at 15 s.
- Copy for Isaac in the UI: plain words, no em dashes.
- Engine tests: `cd engine && .venv/bin/python -m pytest ../tests/engine -q`. Swift tests: `bash scripts/swift_test.sh`.

## Review Focus
1. **Empty or junk class** (mic muted, lid closed all class, under 150 words): no paid call is made. Status is `too_little` and the view shows "Not enough was heard to make notes." Test in Task 2.
2. **Model returns a hallucinated or out-of-range `seg` id, or bad JSON:** the item is dropped; bad JSON retries 3× and then status is `failed` with Retry. Test in Task 2.
3. **App quits or crashes mid-class:** on the next `class.start`, or at engine start, any session still `live` or `paused` with no segment in 30 min is ended, and its notes are made. Test in Task 1.
4. **The weekly priorities rewrite meets class lines:** the `## From class` section is untouched by `apply` and `apply_proposal`, and `due`, `cls` and `done` survive a render. Test in Task 4.
5. **Ask on a class older than 183 days:** it answers from the notes and says "The transcript for this class is gone (kept 6 months)." Test in Task 3.

---

### Task 1: Class store and session RPC

**Files:**
- Create: `engine/src/misty_engine/migration_v8.sql`, `engine/src/misty_engine/classes.py`, `engine/src/misty_engine/api_class.py`
- Modify: `engine/src/misty_engine/db.py:12-16` (add `8:`), `engine/src/misty_engine/api.py:87-92` (merge `class_methods(ctx)`), `engine/src/misty_engine/main.py` (call `classes.close_stale` and `classes.prune` at engine start)
- Test: `tests/engine/test_classes.py`

**Interfaces:**
- Produces:
  - Tables:
    - `class_sessions(id TEXT PK 'cs_'+hex8, subject TEXT, title TEXT, cal_event_id TEXT NULL, started_at REAL, ended_at REAL NULL, status TEXT CHECK IN ('live','paused','ended','noting','noted','too_little','failed'), his_notes TEXT DEFAULT '', notes_json TEXT NULL, cost REAL DEFAULT 0, gaps TEXT DEFAULT '[]')`
    - `class_segments(id INTEGER PK, session_id TEXT REFERENCES class_sessions ON DELETE CASCADE, t_start REAL, t_end REAL, source TEXT CHECK IN ('room','mac'), text TEXT)`. Times are seconds from session start.
    - `class_segments_fts` FTS5 (`text`, content=`class_segments`, with insert and delete triggers)
    - `class_actions(id TEXT PK 'ca_'+hex6, session_id, text, kind CHECK IN ('homework','test','reading','bring','other'), due TEXT NULL 'YYYY-MM-DD', due_text TEXT, seg_id INTEGER, priority_id TEXT NULL, done INTEGER DEFAULT 0)`
  - `classes.start(con, clock, subject: str, title: str | None, cal_event_id: str | None) -> dict`. It ends any other live session first. The default title is `f"{subject}, {d MMM}"`.
  - `classes.add_segment(con, sid, t_start, t_end, source, text) -> int`. It scrubs, and skips empty text.
  - `classes.save_notes(con, sid, text)`, `classes.pause(con, sid, reason: str)` (appends `{from,to:null,reason}` to `gaps`), `classes.resume(con, sid)` (closes the last gap), `classes.end(con, clock, sid)`, `classes.get(con, sid) -> dict` (session, segments, actions), `classes.list(con, subject=None, limit=50) -> list[dict]`, `classes.delete(con, sid)`
  - `classes.close_stale(con, clock) -> list[str]`: any `live` or `paused` session with no segment for 30 min is ended, and its ids are returned.
  - `classes.prune(con, clock) -> int`: deletes segments of sessions started more than 183 days ago.
  - RPC: `class.start {subject, title?, cal_event_id?}`, `class.segment {session, t_start, t_end, source, text}`, `class.notes.save {session, text}`, `class.pause {session, reason}`, `class.resume`, `class.end`, `class.get`, `class.list {subject?}`, `class.delete`. A bad param raises `Fatal("BAD_PARAMS")`, as in `api_chat.py`.

- [ ] **Step 1: Write failing tests** in `tests/engine/test_classes.py`, using the `con` and `clock` fixtures:
  - `test_start_ends_the_other_live_session`
  - `test_segment_is_scrubbed_and_searchable`: add "my key is sk-or-v1-abc123..." plus "Le Chatelier shifts equilibrium". The FTS `MATCH 'chatelier'` hits, and the stored text has no `sk-or`.
  - `test_homework_line_survives_scrub`: "Q1-8 page 112, due 28 Oct" is stored unchanged.
  - `test_pause_and_resume_record_a_gap`: `gaps == [{"from":…, "to":…, "reason":"lid"}]`.
  - `test_close_stale_ends_abandoned_sessions`: no segment for 31 min, so it is ended. At 29 min it is not.
  - `test_prune_drops_old_segments_keeps_notes`: 184 days old means 0 segments and the session row is kept. At 182 days the segments are kept.
  - `test_delete_removes_segments_and_actions`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest ../tests/engine/test_classes.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** the migration, `classes.py` and `api_class.py` with the interfaces above. Register v8 in `db.MIGRATIONS`.
- [ ] **Step 4: Run** the new tests plus `test_db.py`. Expected: PASS.
- [ ] **Step 5: Commit** `Class Notes: class store, segments with search, session RPC`

### Task 2: Making the notes

**Files:**
- Create: `engine/src/misty_engine/class_notes.py`
- Modify: `engine/src/misty_engine/api_class.py` (`class.end` starts `make_notes` as an asyncio task; add `class.regenerate`; emit `class.notes` notifications)
- Test: `tests/engine/test_class_notes.py`

**Interfaces:**
- Consumes: Task 1 `classes.get`, `classes.end`
- Produces:
  - `async class_notes.make_notes(ctx, sid) -> str`, which returns the final status. Statuses: `noting` → `noted` | `too_little` | `failed`.
  - Notification `class.notes {session, status}`
  - `notes_json` shape (the spec's section 4 JSON, after checking):
    `{summary, sections:[{heading, points:[{text, seg:[int], mine:bool}]}], remember:[{kind, text, seg:[int]}], action_items:[{id, text, kind, due, due_text, seg:[int]}], gaps:[...]}`
  - `class_notes.resolve_due(due_text: str, class_date: date) -> str | None`. It handles "tomorrow", "next <weekday>", "<weekday>", "<d> <Mon>", "<Mon> <d>", "in N days" and ISO dates. Anything else gives None.
  - Prompt values:
    - System: "You turn a class transcript into complete study notes for Isaac, an IB student."
    - The user parts: subject, title and date; the segments as `[id] mm:ss text`; his notes with their line timers, or "(he typed nothing)"; and the gaps.
    - Schema name `class_notes`, `model_key="strong"`, `max_tokens=2500`, `purpose="class"`.
  - Too little: fewer than 150 words across all segments. That gives `too_little`, with no call.
  - Retries: on `Retryable` or `JSON_PARSE`, wait 2 s, 8 s, then 30 s, and after the third failure set `failed`.
  - Actions: insert `class_actions` rows from `action_items`, with `due` from `resolve_due` when the model's `due` is missing or invalid.
  - Wiki recall: `ctx.wiki.write_text(f"classes/{subject_slug}.md", ...)` appends `## <title> (<date>)` + the summary + the Remember bullets. Skip it if `ctx.wiki is None`.

- [ ] **Step 1: Write failing tests**, with a fake `ctx.runner` whose `run()` returns a scripted `RunResult`:
  - `test_full_notes_even_with_no_typed_notes`
  - `test_points_with_unknown_seg_are_dropped`: seg 999 is dropped; seg 1 is kept.
  - `test_his_lines_kept_word_for_word`: his "le chat = stress → shift" appears exactly once with `mine: true`.
  - `test_too_little_makes_no_call`: runner called 0 times; status `too_little`.
  - `test_bad_json_retries_then_fails`: 3 calls; status `failed`. Monkeypatch the sleeps.
  - `test_due_dates_resolve_in_sgt`: class on Fri 2026-10-23. "next Tuesday" gives 2026-10-27, "tomorrow" gives 2026-10-24, "28 Oct" gives 2026-10-28, and "soon" gives None.
  - `test_actions_saved_with_seg`
  - `test_regenerate_replaces_notes_and_actions`
  - `test_runs_while_background_work_paused`: with `settings paused=true`, the status is still `noted`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `class_notes.py` and the RPC wiring.
- [ ] **Step 4: Run** the Task 1 and Task 2 tests. Expected: PASS.
- [ ] **Step 5: Commit** `Class Notes: full notes after class, checked against the transcript`

### Task 3: Ask the class, plus chat and voice tools

**Files:**
- Create: `engine/src/misty_engine/class_ask.py`
- Modify: `engine/src/misty_engine/api_class.py` (`class.ask`), `engine/src/misty_engine/chat.py` (two tools in the tool list and `run_tool`)
- Test: `tests/engine/test_class_ask.py`, plus additions to `tests/engine/test_chat.py`

**Interfaces:**
- Consumes: Task 1 tables and FTS, Task 2 `notes_json`
- Produces:
  - `class_ask.retrieve(con, question, *, sid=None, subject=None, since=None, k=8) -> list[dict]`. It matches FTS5 on the question's words (OR-joined, words over 2 characters), adds ±1 neighbouring segment, and returns `[{session, seg, t_start, text}]`.
  - `async class_ask.ask(ctx, question, *, sid=None, subject=None, since=None) -> {"answer": str, "cites": [{session, seg, t_start}]}`. It makes one `ctx.runner.run` call (`purpose="class"`, `model_key="haiku"`, `max_tokens=500`). Cites not in the retrieved set are dropped.
    - When the session's segments are pruned, it answers from `notes_json` and starts the answer with "The transcript for this class is gone (kept 6 months)."
    - With no hits, it answers "I couldn't find that in the class." and makes no call.
  - Chat tools:
    - `class_search(query: str, subject?: str, since?: "YYYY-MM-DD")` calls `class_ask.ask`.
    - `class_notes(which: "last" | session id, subject?: str)` returns the summary, the sections and the action items as text.
- [ ] **Step 1: Write failing tests:**
  - `test_retrieve_finds_neighbours`
  - `test_ask_during_live_class_uses_segments_so_far`
  - `test_ask_drops_made_up_cites`
  - `test_ask_old_class_uses_notes_and_says_so`
  - `test_no_hits_no_call`
  - In `test_chat.py`, `test_class_notes_tool_last_physics`, using `ScriptBrain` with a `class_notes` call. The reply round's body contains that class's summary.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest ../tests/engine -q`. Expected: PASS.
- [ ] **Step 5: Commit** `Class Notes: ask the class, and chat/voice can read classes`

### Task 4: Class action items on Priorities

**Files:**
- Modify: `engine/src/misty_engine/wiki/priorities.py`, `engine/src/misty_engine/api_wiki.py:203-233`, `engine/src/misty_engine/class_notes.py` (add lines after notes), `engine/src/misty_engine/api_class.py` (`class.action.done`)
- Test: `tests/engine/test_priorities_class.py`

**Interfaces:**
- Consumes: Task 2 `class_actions` rows
- Produces:
  - `LINE_RE` accepts optional ` due:YYYY-MM-DD`, ` cls:<session id>` and ` done:1` inside the comment. `Line` gains `due: str|None`, `cls: str|None` and `done: bool`.
  - Class lines live under `## From class`. `apply` and `apply_proposal` and the weekly diff never add, remove or reword lines there.
  - `priorities.add_class_items(store, items: list[dict]) -> list[str]` returns the priority ids. The text is `"<Subject>: <action text>"`, and the area is the subject slug.
  - `priorities.set_done(store, pid, done: bool)`. Lines done for more than 7 days, or not done and more than 7 days overdue, are removed at the next weekly apply.
  - RPC `class.action.done {action, done}` updates both `class_actions.done` and the priority line. `priorities.get` returns `due`, `cls` and `done` per line, and a `priorities.done {id, done}` RPC calls the same sync.
- [ ] **Step 1: Write failing tests:**
  - `test_class_lines_survive_weekly_apply`
  - `test_fields_round_trip_render`
  - `test_done_syncs_both_ways`
  - `test_old_done_lines_cleared_after_7_days`
  - `test_notes_add_priority_lines`: after `make_notes`, the action rows have `priority_id` set.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** all engine tests. Expected: PASS.
- [ ] **Step 5: Commit** `Class Notes: action items land on Priorities with due dates, done syncs both ways`

### Task 5: Class session controller (app)

**Files:**
- Create: `app/Misty/Core/Class/ClassVM.swift`, `app/Misty/Core/Class/ClassModels.swift`
- Modify:
  - `app/Misty/Core/Voice/VoiceVM.swift:161-162, 367-368`. When `classMode` is on, finals go to `ClassVM.heard(text:start:end:)` and partials go to `ClassVM.partial`. "Misty, class mode" calls `ClassVM.start(subject: nil)`.
  - `app/Misty/Core/Voice/Endpointer.swift`: add a `classTuned()` preset with `minLevel 0.0008`, `ratio 2.2` and `endMs 600`. `maxSeconds` stays 15.
  - `app/Misty/Core/AppState.swift`: `public let classes: ClassVM`. Route the `class.notes` notification.
  - `app/Misty/Core/RpcEvent.swift`: add `.classNotes(session:status:)`.
- Test: `app/MistyTests/ClassVMTests.swift`

**Interfaces:**
- Consumes: Task 1 to 3 RPC
- Produces (`@MainActor @Observable public final class ClassVM`):
  - State: `session: ClassSession?`, `segments: [ClassSegment]`, `partial: String`, `state: .idle | .warming | .live | .paused(reason) | .ending`, `elapsed: TimeInterval`
  - `start(subject: String?) async`. A nil subject means "Pick a subject" is shown. It turns VoiceVM live mode on in class mode if it is off, so the mic and Ears are VoiceVM's own. It shows `.warming` until `ears.warm()` is done.
  - `heard(text:start:end:)` posts `class.segment` with times relative to session start.
  - `notesChanged(_ text: String)` debounces 1 s, then calls `class.notes.save`. His line timers are `[mm:ss]` prefixes the editor adds at line start.
  - `pause(reason:)`, `resume()`, `end() async`, `ask(_ q: String) async -> ClassAnswer`
  - Lid: on `NSWorkspace.willSleepNotification` it pauses with reason `"lid"`, and on `didWakeNotification` it resumes if the session is still open.
  - Auto-end: 15 min with no final segment ends the session and posts a notification "Class ended (nothing heard for 15 min)".
  - `ClassModels.swift`: Codable `ClassSession`, `ClassSegment`, `ClassNotes`, `ClassAction`, `ClassAnswer`, matching the Task 1 to 3 JSON.
- [ ] **Step 1: Write failing tests** with the fake `EngineAPI` from `TestSupport.swift`:
  - `testHeardSendsRelativeTimes`
  - `testSleepPausesAndWakeResumes`
  - `testFifteenMinutesQuietEnds`: inject the clock.
  - `testNotesSaveDebounced`: 3 edits in 0.5 s give 1 call.
  - `testClassModeFinalsGoToClassNotChat`: a final in class mode makes zero `chat.send` calls and one `class.segment`.
- [ ] **Step 2: Run** `bash scripts/swift_test.sh`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS, with the existing VoiceLoopTests still passing.
- [ ] **Step 5: Commit** `Class Notes: class sessions in the app, sharing the mic and ears with live mode`

### Task 6: Side panel, Classes tab, Priorities checkboxes (plain look)

**Files:**
- Create: `app/Misty/Views/Class/ClassPanel.swift` (an `NSPanel` host, reusing the window setup in `StreamCursor.swift` and `BorrowNotice.swift`, but floating, movable and not click-through), `app/Misty/Views/Class/ClassPanelView.swift`, `app/Misty/Views/Class/ClassesView.swift`, `app/Misty/Views/Class/ClassNotesView.swift`
- Modify: `app/Misty/Views/RootView.swift:3-37` (add `case classes = "Classes"`, shortcut "5"), `app/Misty/Views/MenuContent.swift` (Start class / End class), `app/Misty/Views/PrioritiesView.swift` (checkboxes and due date on `## From class` lines), `app/Misty/Views/SettingsView.swift` (subject names: Chem, Math, Physics, Econ, English, Spanish, editable)

**Interfaces:**
- Consumes: Task 5 `ClassVM`, Task 4 `priorities.done`
- Produces: UI only, in the current `Theme.swift`. Nothing new visually.
  - **Panel:** 380 pt wide. Tabs: Notes (default) / Transcript. Ask box at the bottom. A timer, a red "transcribing" dot that is always visible while live, and Pause / End. It collapses to a pill with the timer and the dot.
  - **Classes tab:** classes grouped by subject, newest first. The search field at the top calls `class.ask` across classes, with a subject filter.
  - **Transcript:** auto-scrolls until he scrolls up, then a "Jump to now" button appears. The partial line is grey.
  - **Notes view:** Summary, sections (his lines in primary text, Misty's in secondary), Remember, Action items, and a "Missed: 10:14 to 10:52 (lid closed)" line from `gaps`. Each item has an `mm:ss` chip that opens the Transcript at that segment.
  - Buttons: Regenerate, Copy as Markdown, Delete class (with a confirm). Status copy: `noting` → "Making your notes…", `too_little` → "Not enough was heard to make notes.", `failed` → "Notes not made yet" + Retry.
- [ ] **Step 1:** Build with `bash scripts/dev_app.sh`. Expected: succeeds.
- [ ] **Step 2: Silent end-to-end check** (no sound out loud):
  - Make a 6-minute Chem lesson WAV with `say -o` from a script that includes "homework: questions 1 to 8 on page 112, due next Tuesday".
  - Run the app with `MISTY_CLASS_WAV=<path>`, a debug-only env var that makes `ClassVM` feed the file to Ears in place of the mic.
  - Check that the transcript fills, End gives notes with chips, a Chem homework line with due 2026-10-27 appears in Priorities, and ticking it there ticks it in the notes.
- [ ] **Step 3:** Run `bash scripts/swift_test.sh` and the engine tests. Expected: PASS.
- [ ] **Step 4: Commit** `Class Notes: side panel, Classes tab, Priorities checkboxes`

### Task 7: Isaac's real-class test
- [ ] Isaac runs one real lid-open class. Afterwards, read `voice.log` and `engine.log` (the `RUN schema=class_notes` cost and the ears timings). Note word accuracy from his opinion of the notes, then tune the `classTuned()` thresholds if the teacher was missed.
