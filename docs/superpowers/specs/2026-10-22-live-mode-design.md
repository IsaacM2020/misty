# Live mode (voice, round 3) design

Status: built 2026-10-22 (6b8d7ca fixes + caching, e5bede7 live mode). Isaac changed: no on-device model, Jev classifies every unclear phrase (ZDR), consent sheet before first use.

## Why
Isaac's hotkey test (2026-10-22) showed the session already behaves like an always-on mode, so live mode is the default.
Control+Option only turns it on or off. Problems seen in the logs:
- She cut him off at a pause ("generate a paragraph about Rubik's..."). The rest ("make it fifty words or less")
  arrived while she was thinking and was dropped.
- Chatter heard while she ran a task ("I don't know") cancelled the turn, so no reply was spoken.
- The Stream run for "minimise all the tabs" looped 20 steps ($0.0093, 50 s). It hit Hide Others (cmd+alt+h) and
  pressed WhatsApp's green zoom button thinking it was minimise, which made WhatsApp full screen.
- The chat turn cost (meta usd) leaves out the Stream run's spend.

## Behaviour
- **Toggle:** Control+Option turns live mode on or off. The pill shows "Live" while it's on. The mic stays open, and
  speech-to-text runs on the Neural Engine. Nothing leaves the Mac unless it is addressed to Misty.
- **Addressee:** each finished phrase is classified on-device by Apple Foundation Models as `misty`, `other` or `background`.
  - Fast paths skip the model: her name, a follow-up within ~8 s of her reply, or an answer while she's confirming.
  - `misty`: answered as now.
  - `other`: silent, but checked for a to-do aimed at Isaac (below).
  - `background`: dropped.
  - If unsure: stay quiet. Unsure phrases are never sent to the cloud (Isaac's answer is pending; recommended).
- **Overheard to-dos (Isaac's choice):** only actionable items aimed at Isaac ("dinner at 8", "submit the form by
  Friday") are saved, as short notes he can see and delete. No transcripts. Audio is never saved.
- **End of turn:** when he goes quiet, an on-device check asks whether the sentence is finished.
  - Unfinished: a trailing connective, "like" or "the", or a cut-off clause. She waits up to ~2 s more.
  - Finished: send it.
- **Merge:** if he speaks again within ~4 s of a phrase being sent and before she starts speaking, the turn is stopped. Both
  parts are joined into one prompt and re-sent.
- **While she speaks:** unchanged. Only a stop word or a tap cuts her off, because of echo.
- **While she works (thinking):** a new phrase merges (rule above) or waits. It never cancels the turn.
- **Class mode:** silent and takes no actions. Note-taking (Granola-style, linked to his calendar) is a later, separate
  build. Isaac also wants to study Granola first.

## Fixes bundled
- Stream gets real `clear_desktop` (hide every app except Misty, never quit) and `minimise_all` actions.
- Stream stops after the same action fails twice, instead of running to the step limit.
- Chat turn usd includes Stream run spend.

## Out of scope
- Class notes, recordings and calendar link: the next build.
- Headphone-only plain talk-over.
