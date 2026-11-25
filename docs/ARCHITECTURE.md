# Misty: how it works

The full technical map of Misty, written so Isaac can read it end to end and so any future Claude session can rebuild its understanding of the system without reading 22,000 lines of code.

- **Last checked against the code:** 2026-10-23, branch `stream`, commit `5125367`.
- **Repo:** `<repo>`. **Data:** `~/Misty`.
- **Size:** about 22k lines (Python engine + Swift app), 935 engine tests, 162 Swift tests, 157 commits.
- **Keep it true.** When a change makes a section wrong, fix that section in the same commit. Section 18 (current state) goes stale fastest.

## Contents

1. [The big picture](#1-the-big-picture)
2. [Repo map](#2-repo-map)
3. [Runtime: processes, startup, files on disk](#3-runtime-processes-startup-files-on-disk)
4. [The database](#4-the-database)
5. [The cloud choke point: models, money, caps](#5-the-cloud-choke-point-models-money-caps)
6. [Privacy and safety rules](#6-privacy-and-safety-rules)
7. [Memory pipeline: from your stuff to notes](#7-memory-pipeline-from-your-stuff-to-notes)
8. [Search and context packs](#8-search-and-context-packs)
9. [Registry and wiki](#9-registry-and-wiki)
10. [Suggestions, prep and Priorities](#10-suggestions-prep-and-priorities)
11. [Chat](#11-chat)
12. [Stream: hands on the Mac](#12-stream-hands-on-the-mac)
13. [Voice](#13-voice)
14. [Live mode](#14-live-mode)
15. [Class Notes](#15-class-notes)
16. [The Swift app](#16-the-swift-app)
17. [Build, run, test, debug](#17-build-run-test-debug)
18. [Current state and known issues](#18-current-state-and-known-issues)
19. [Glossary](#19-glossary)

---

## 1. The big picture

Misty is a private Mac assistant for one person. She has five jobs:

1. **Remember.** She reads mail, calendar, WhatsApp, iMessage, files and Claude Code memories, and compiles them into a linked wiki plus a search index.
2. **Notice.** When something needs Isaac, it becomes a card. She gets each card ready in the background, so one tap does it.
3. **Talk.** A chat (typed or spoken) with tools: remember facts, search notes, search the web, look at the screen, do maths, act on the Mac.
4. **Act.** Stream drives other apps through Accessibility, with a visible cursor and a safety gate.
5. **Listen.** Voice on a hotkey, live mode (always-on mic, no wake word), and class notes (a transcript, then full notes).

### Two processes, one socket

```
┌────────────────────────── Misty.app (Swift, SwiftUI) ──────────────────────────┐
│  UI: Chat · Suggestions · Priorities · Memory(graph) · Classes · Settings       │
│  Voice: mic + echo cancel → Endpointer → Ears (Phonon-2, Neural Engine)         │
│         Speaker (Phonon TTS, Neural Engine) · VoicePill · SummonKey             │
│  Hands: Accessibility tree, CGEvent input, window capture, StreamCursor          │
│  EngineSupervisor: launches + restarts the engine                               │
└───────────────▲─────────────────────────────────────────────┬──────────────────┘
                │  JSON-RPC 2.0, one JSON object per line      │
                │  Unix socket ~/Misty/run/engine.sock         │
┌───────────────┴──────────────── engine (Python 3.12) ────────▼──────────────────┐
│  RpcServer → api*.py methods        Worker + Scheduler (job queue in SQLite)    │
│  Sources → prep (scrub, chunk, embed on Mac) → Jev triage → Haiku compile       │
│  Registry + wiki · Search · Chat · Stream brain · Class notes · live.hear       │
│  OpenRouterClient.post(): THE one door to the cloud (ZDR, paid lock, caps)      │
└──────────────────────────────────────────┬──────────────────────────────────────┘
                                           │ HTTPS, zero data retention
                                    OpenRouter → Anthropic (Haiku 5.5, Opus 5.5),
                                    TypeSafe (Jev), Qwen (cheap answerer, Stream brain option)
```

**Why it's split this way.** The engine is Python because the ML and data tools live there (MLX embeddings, sqlite-vec, Google APIs). The app is Swift because macOS permissions (Accessibility, Screen Recording, Microphone, Input Monitoring) belong to a signed app bundle. A child Python process doesn't inherit them. So the app does everything that touches the screen, keyboard or mic. The engine does everything that touches data and models. They talk over one socket.

**What runs where.**

| On the Mac (free, private) | In the cloud (paid, ZDR) |
|---|---|
| Secret scrubbing, chunking, file walking | Jev: yes/no and multiple-choice judgements |
| EmbeddingGemma 2 embeddings (MLX) | Haiku 5.5: notes, chat, summaries, Stream brain, class notes |
| Search (FTS5 + vectors) | Opus 5.5: life areas, the about card, rare "strong" jobs |
| Phonon-2 speech to text (Neural Engine) | |
| Phonon text to speech (Neural Engine) | |
| Accessibility reading and pressing | |

---

## 2. Repo map

```
misty/
├── PRODUCT.md              product truth: who it's for, look and feel direction
├── README.md               hard rules + engine commands
├── config/
│   ├── pinned.toml         models, prices, embedding model, thresholds, Stream brains (the source of truth)
│   ├── bootstrap-about-isaac.md   about card used until the wiki writes its own
│   └── signing.local       codesign identity (local only)
├── engine/                 Python (uv). src/misty_engine/…
│   ├── main.py             Engine: builds Ctx, starts RPC, worker, scheduler, watchers, warm-ups
│   ├── rpc.py              JSON-RPC server over the Unix socket
│   ├── api.py              registers every RPC method (api_chat, api_class, api_setup, api_wiki, stream/api)
│   ├── ctx.py              Ctx: the shared bag every handler gets (db, settings, queue, clients, wiki…)
│   ├── config.py           pinned.toml loader + Settings (DB-backed key/values with defaults)
│   ├── openrouter.py       THE cloud client. post() = paid lock → online → key → ZDR → scan → cap → send → spend
│   ├── runner.py           ApiRunner.run(): system + parts → JSON reply (used by everything but compile + chat)
│   ├── spend.py            spend log, daily/build caps, per-purpose sub-caps
│   ├── scrub.py            local secret and PII scrubber (regex + entropy + Luhn)
│   ├── jobs.py, scheduler.py, pipeline.py, live.py, cloud.py   the memory pipeline
│   ├── sources/            gmail, gcal, imessage, whatsapp(+export), folders, repos, claude_mem, contacts, graphify
│   ├── index.py, search.py, embed.py, chunk.py   search
│   ├── registry/           people/projects/topics as rows, resolution, merges, rendering to pages
│   ├── wiki/               notes, summaries, areas, priorities, context packs, ask, graph, health, synth
│   ├── compile/            Haiku compile prompts, batching, parsing
│   ├── triage.py, jev.py, jev_wire.py   Jev judgements
│   ├── suggest.py, prep.py cards and their background plans
│   ├── chat.py, api_chat.py, facts.py   chat + facts
│   ├── stream/             loop, brain, gate, observe, actions, bridge, appcards, log
│   ├── hearing.py          live mode's "who was that for?"
│   ├── classes.py, class_notes.py, class_ask.py, api_class.py   Class Notes
│   └── cli.py              `misty …` commands
├── app/                    Swift. project.yml (XcodeGen) → Misty.xcodeproj
│   ├── Misty/Core/         AppState, RpcClient, EngineSupervisor, view models, Theme
│   │   ├── Voice/          AudioIO, Endpointer, Ears, Speaker, SentenceSplitter, SummonKey, VoiceVM, VoicePill
│   │   ├── Stream/         SystemHands, SnapshotWalker, Capture, Input, HandsService, LastApp, StreamCursor
│   │   └── Class/          ClassVM
│   ├── Misty/Views/        RootView (tabs), ChatView, SuggestionsView, PrioritiesView, GraphScreen, Class/…
│   ├── Misty/Setup/        first-run setup flow
│   ├── MistyTests/         162 Swift tests
│   └── Vendor/phonon-coreml/   vendored Phonon-2 Core ML runtime (1.1.2)
├── scripts/                dev_app.sh, bundle_engine.sh, fetch_*_model.sh, swift_test.sh, install_app.sh…
├── tests/engine/           935 Python tests (92 files)
└── docs/                   this file, specs + plans (docs/superpowers), verify notes (docs/verify), checklists
```

---

## 3. Runtime: processes, startup, files on disk

### Startup, step by step

1. **App launch** (`AppState.start()`):
   - creates `~/Misty/run` with mode 700
   - starts `EngineSupervisor` with a `RealLauncher`, which runs the bundled Python at `Misty.app/Contents/Resources/engine`, or `MISTY_ENGINE_CMD` in dev
   - starts `LastApp` (remembers which app you were in before Misty)
   - starts `SummonKey` (Control+Option)
   - calls `voice.warm()`
2. **Engine `serve()`** (`main.py`):
   - takes an `flock` on `~/Misty/run/engine.lock`, so there's only ever one engine
   - points `HF_HOME` at `~/Misty/models`, so Delete-all removes the models too
   - runs `Engine.run()`
3. **`Engine.run()`**:
   - connects SQLite and runs migrations
   - builds `Ctx`: settings, job queue, spend meter, net state, OpenRouter client, embedder, index, Jev, wiki store, runner
   - recovers jobs left `running` by a crash
   - closes stale classes and prunes transcripts older than 6 months
   - starts the RPC server, plus the live worker, scheduler and file/chat watchers
   - warms the embedder (about 8 s cold)
   - re-tries any class still waiting for notes
   - watches the parent PID (exits within 1 s if the app dies, so no orphan engines)
4. **Supervisor**:
   - restarts the engine if it dies, with backoff of 2, 4, 8, 16, then 30 s
   - resets the backoff after 60 s of stable running
   - shows `crashLoop` after 3 crashes in 5 minutes

### RPC

- One JSON object per line, JSON-RPC 2.0. Requests have an `id`. Notifications have no `id`, and the engine pushes them to the app.
- **Each request runs as its own task.** A long `chat.send` must not block the `hands.result` it's waiting on over the same connection.
- **Errors never carry content.** A `MistyError` becomes `-32000` with its code (like `PAID_LOCKED`). Anything else becomes `-32603 INTERNAL`, and only the exception class is logged.
- **Engine → app notifications:**
  - `status.changed` (every 2 s)
  - `chat.delta` / `chat.status` / `chat.confirm`
  - `hands.request`
  - `suggestions.progress`
  - `class.notes`
  - `build.progress`
- **Methods (app → engine)**, grouped:
  - `chat.*`: send, stop, answer, history
  - `class.*`: start, segment, pause, resume, end, get, list, ask, regenerate, delete, notes.save, action.done
  - `suggestions.*`, `priorities.*`, `facts.*`
  - `entity.*` / `entities.lookup`, `graph.get`, `page.get`
  - `setup.*` (Google auth, OpenRouter key, chat pickers, build)
  - `control.*` (pause, resume, raise_cap)
  - `hands.*` (register, result, user_input), `stream.run`, `live.hear`
  - `diag.*`, `data.delete_all`, `settings.set`, `status.get`

### `~/Misty` on disk

| Path | What |
|---|---|
| `misty.db` (+ `-wal`, `-shm`) | everything structured (section 4) |
| `wiki/` | the Markdown wiki: `about-isaac.md`, `index.md`, `priorities.md`, `people/`, `areas/`, `classes/`, `answers/`, `log.md`, `health.md`, `corrections.md` (1,920 pages) |
| `logs/engine.log` | engine events: keys, ids, counts, ms, usd. Never content |
| `logs/voice.log` | the app's voice log (warm-up, partials, finals, routing, timings) |
| `run/engine.sock`, `run/engine.lock` | socket + single-engine lock |
| `models/` | Hugging Face cache (EmbeddingGemma) |
| `stream/runs/*.jsonl` | one file per Stream run (local only, holds screen text) |
| `stream/appcards/` | cached App Cards (a week) |
| `backup/`, `replays/`, `tapes/`, `tmp/`, `cache/` | dev and test leftovers |

Outside `~/Misty`:

- `~/Library/Application Support/Misty/Phonon-2-CoreML/` holds the ears model.
- `~/Library/Caches/phonon-coreml/<key>/` holds the compiled ears model.
- `~/Library/Caches/com.isaacmiranda.Misty/com.apple.e5rt.e5bundlecache/` is the Neural Engine's compile cache (section 18).
- Keys live in the macOS Keychain only (`keychain.py`).

---

## 4. The database

One SQLite file in WAL mode, migrated by `db.migrate` (schema plus migrations v2, v6, v7, v8). Everything touching SQLite runs on the engine's event-loop thread. Heavy work (extract, embed) runs in threads and hands back plain values.

| Table | Holds |
|---|---|
| `items` | one row per thing read (file, mail thread, chat week, calendar week…). `status` goes `found → scrubbed → triaged → compiled → embedded → linked → done` (or `skipped`, `compile_failed`). Also stores `matters`, `area`, `note_path`, `meta` (JSON), `fingerprint` |
| `item_chunks` | scrubbed text cut into chunks (this is the only text kept from a source) |
| `fts` (FTS5) | keyword index over chunks (positive rowids) and wiki pages (negative rowids) |
| `vectors` (sqlite-vec) + `vec_refs` | 768-d embeddings, with what each one is (chunk, page, image, item) |
| `jobs` | the job queue (kind, item, payload, status, attempts, next_run_at, error_code) |
| `spend` | every paid call: ts, provider, model, job, build, usd, purpose |
| `settings` | JSON key/values over code defaults (`config.DEFAULTS`) |
| `sources_state` | per-source cursor, health, last ok |
| `entities`, `entity_aliases`, `entity_handles`, `entity_merges`, `entity_redirects`, `entity_text`, `entity_vec`, `mentions`, `judgements` | the registry (section 9) |
| `facts` | things Isaac told her ("my English task is this doc"), one active fact per key |
| `chat_messages` | the chat thread (`user` / `assistant` / `summary` rows, with `meta` JSON holding tool calls and cost) |
| `suggestions` | cards and their plans |
| `class_sessions`, `class_segments` (+ FTS), `class_actions` | Class Notes |
| `act_runs`, `act_steps`, `act_notes`, `act_skills`, `act_window_snapshots` | Stream and hands history |
| `builds` | one row per memory build |

**Real numbers (2026-10-23):**

| Thing | Count |
|---|---|
| Items | 6,515 (claude_mem 3,438 · folders 1,540 · gmail 980 · whatsapp 360 · imessage 100 · gcal 78 · repos 19) |
| Entities | 4,316 (458 people · 688 projects · 3,170 topics) |
| Facts | 9 |
| Chat messages | 289 |
| Cards | 28 (21 done, 6 dismissed, 1 new) |
| Classes | 2 sessions, 516 segments |

---

## 5. The cloud choke point: models, money, caps

### One door

Every cloud call goes through `OpenRouterClient.post()` (`openrouter.py`). A guard test fails if any other path appears. Inside `post()`, in this order:

1. **Paid lock.**
   - If `settings.paid_calls_allowed` is false and the call isn't inside `user_approved()`, it raises `PAID_LOCKED`.
   - `user_approved()` is a context variable. It means "Isaac asked for this himself": a chat message, Go on a card, ending a class, a hearing call while live mode is on.
2. **Online.** It uses `NetState`, which a probe updates every 15 s.
3. **Key** from the Keychain.
4. **ZDR.** `with_zdr()` forces `provider.zdr = true` and `data_collection = "deny"`. A body asking for `zdr: false` raises `PrivacyViolation`.
5. **Secret re-scan.** `assert_clean()` walks every string in the payload through the scrubber's detector. A hit raises, and nothing is sent.
6. **Cap check.** `SpendMeter.check()` covers the daily cap (live) or the build cap (build), then the purpose sub-cap.
7. **Send.** A normal POST, or streaming SSE when `on_delta` is given (chat).
8. **Map errors.**
   - Connect errors set offline and raise `NetworkDown`.
   - Timeouts raise `Retryable`.
   - HTTP errors are mapped by `_interpret`.
9. **Record spend** using the real cost from `usage.cost`, falling back to the estimate. It logs `OR_CALL model= ms= usd= tokens_in= cached=`.

`ApiRunner.run()` (`runner.py`) sits on top for one-shot JSON jobs:

- system prompt + parts → `client.chat()` → slice `{…}` → `json.loads`
- If the reply isn't JSON, it raises `Retryable("JSON_PARSE")` and attaches `cost_usd` (it was paid for) and `truncated` (`finish_reason == "length"`).
- Retries with backoff (5, 30, 120 s, 3 tries) are the job queue's job, not the runner's.

### Models (`config/pinned.toml`)

| Key | Model | Used for | $/1M in · out |
|---|---|---|---|
| `jev` | `typesafe/jev-1.13` (System One endpoint) | worth / triage / hearing judgements, returns choices + confidence | 0.042 · 0 |
| `haiku` | `anthropic/claude-haiku-5.5` (Vertex global, then Bedrock; reasoning off) | compile, summaries, chat, Stream brain, class notes, class ask | 0.10 · 0.50 (cache read 0.01) |
| `strong` | `anthropic/claude-opus-5.5` (reasoning effort low, which is required on this route) | life areas, about card, rare strong summaries | 4.00 · 20.00 |
| `answer_cheap` | `qwen/qwen3-30b-a3b-instruct-2507` | eval model-swap tests | 0.10 · 0.30 |
| Stream brain `qwen` | `qwen/qwen3.8-27b` | alternative Stream brain | 0.035 · 2.20 |

Haiku has reasoning turned **off** on purpose. Its hidden thinking tokens ate the output cap and cut replies off (that was the first dry-run bug).

### Caps (`spend.py` + settings)

- **Daily cap** (`cap_daily_usd`): 0.20 by default. **Currently 1.50 in Isaac's settings.** Resets at midnight SGT.
- **Build cap** (`cap_build_usd`): 3.00 for a first build.
- **Purpose sub-caps per day:** `judge 0.01, summarise 0.03, ask 0.05, core 0.02` by default. Isaac's settings add `act 0.10, act_escalate 0.05`. A purpose over its sub-cap waits (`waiting_cap`) while everything else keeps running.
- **Feature caps inside the code:**

| Feature | Cap |
|---|---|
| Chat | $0.02 a message, 4 tool rounds |
| Stream | $0.10 and 20 steps a run |
| Hearing | $0.05 a day |
| Class notes | $0.05 a class, across all tries |

### Prompt caching

Chat and Stream mark the fixed part of the system prompt with `cache_control: ephemeral` (rules, date, about card, facts). Later rounds and messages then pay about a tenth for those tokens. `OR_CALL … cached=` shows it working.

### Where the money went (since 2026-10-14, $4.01 total)

| Purpose | USD | Note |
|---|---|---|
| (unlabelled, the first build) | 1.15 | one-off compile of the memory |
| class | 0.74 | 2026-10-23 bug: Opus notes cut off, retried at full price. Fixed (section 15) |
| act + act_spike + act_escalate + stream | 1.19 | Stream development and testing |
| judge | 0.42 | one-off registry clean-up |
| summarise + compile | 0.34 | |
| eval | 0.09 | |
| chat | 0.06 | 289 messages |
| jev, compact, prep, about | 0.02 | |

---

## 6. Privacy and safety rules

These are in the code, and most have tests.

- **Read-only on Isaac's data, except these writes Isaac chose (D0, 16 Nov 2026):**
  - Gmail reads are only `getProfile`, `threads.list`, `threads.get`, `history.list`. The one write is creating a **draft** (`gmail.compose`, move `mail_draft`). She never sends mail: sending is always his click.
  - Calendar: she may **add** an event (`calendar.events`, move `calendar_add`), only on his yes, read back by id. Undo deletes only events carrying her private marker. She never edits or deletes any other event.
  - iMessage: she may **send** one message to one person on his yes (AppleScript, move `message_imessage`), verified from the Messages database. Never a group, never more than one recipient.
  - WhatsApp: she only **opens the chat with the text filled in** (move `message_whatsapp`); he presses Enter. She never says it was sent.
  - Focus: only through his own Shortcuts named Focus On and Focus Off.
  - claude-mem is read-only. She never edits his files.
  - Every write goes through the move registry: risk fixed in code, preview shown word for word, silence is a no, and text she read can't quietly trigger one.
- **Scrub before anything leaves the Mac** (`scrub.py`, pure code, no LLM):
  - Regexes for keys, tokens, passwords and card numbers (Luhn check).
  - High-entropy strings.
  - Context-specific rules for `file` / `mail` / `message`.
  - Secret files (`.env*`, `*.pem`, `*.key`, `id_rsa*`, keychains, password-manager exports, anything named secret/token/password) are indexed **by filename only and never opened**.
- **ZDR on every request**, forced in one function (section 5).
- **No content in logs.** `engine.log` holds event names, ids, counts, timings and dollars. `voice.log` does hold heard text (it's local and a debug aid). It's the one exception.
- **Keys only in the Keychain.**
- **Private chats** (`context_private_chats`): kept out of context packs, summaries, the graph, and anything sent to a model, unless Isaac asks for that chat by name.
- **Audio is never saved.** Ears work on in-memory samples. `MISTY_EARS_DUMP` is a debug-only switch that's off by default. Screenshots from `look` go to the model and are never stored. Chat history only keeps "looked at his screen: <app>".
- **Stream's gate** (`stream/gate.py`) is plain code that the brain can't talk past:
  - **ask** (stops for Isaac's yes): buy, send, post, delete, submit, plus keys that send or delete with no button.
  - **never**: typing a password or card number.
  - Keys and clicks only go to an app that's verified to be in front.
  - Secret-shaped screen text is scrubbed before the brain sees it.
- **Never send, delete, buy or pay without Isaac.** Drafts only.
- **Google account:** you@example.com only.

---

## 7. Memory pipeline: from your stuff to notes

### Sources (`sources/`)

| Source | Item = | Notes |
|---|---|---|
| `folders` | one file | Desktop, Downloads, Notes, Code, Projects, Pictures, minus excluded paths (Evie, `_archive`, `~/Library`, `~/Misty`…). Never follows symlinks, never downloads iCloud placeholders |
| `repos` | one code repo | comes through the folders source |
| `gmail` | one thread | last 18 months. A free local pre-filter (`mailfilter.py`) drops junk from labels and headers only, and junk is never stored. `classroom.google.com` is always kept |
| `gcal` | one ISO week | primary calendar only |
| `imessage` | one chat-week | `~/Library/Messages/chat.db`, needs Full Disk Access, skips excluded chats |
| `whatsapp` | one chat-week | WhatsApp Desktop's local DB, only chats Isaac picked, needs Full Disk Access. Exports are also supported |
| `claude_mem` | one memory | already summarised, so it's **never sent to an LLM**, only indexed |
| `contacts`, `graphify` | helpers | names and handles, the knowledge graph |

### The job queue (`jobs.py`)

- SQLite rows with statuses: `queued`, `running`, `waiting_network`, `waiting_cap`, `done`, `failed`.
- `Retryable` is retried after 5, 30 and 120 s, then fails. `NetworkDown` waits for the network. `CapHit` waits for the next budget window. `Fatal` fails at once.
- Enqueueing the same item and kind while one is already waiting is a no-op, so restarts don't pile up duplicates.
- `claim()` returns nothing while `settings.paused` is true. **This is the Pause everything toggle** (section 18).

### The worker and the scheduler

- **Worker** (`pipeline.Worker`): claims jobs per kind, with a concurrency limit for each:
  - discover 1, prep 2, triage 4, compile_batch 2, everything else 1
  - In the `live` phase, it claims live kinds and build kinds.
  - While a build runs, the build's own worker owns `embed_note`, `resolve`, `compile_batch` and `summarise`.
- **Scheduler** (`scheduler.py`): a table of named entries ticked once a second, all in Asia/Singapore time:

| Entry | Every |
|---|---|
| poll_gmail, poll_gcal | 60 s |
| probe_net | 15 s |
| status_push (to the app) | 2 s |
| watchers (drain file/chat changes) | 1 s |
| index_md, resolve, prep_cards, release_snoozes | 60 s |
| live_compile (Haiku sweep of triaged items) | 300 s |
| summarise | 300 s |
| daily_reset | SGT midnight |
| health | 03:00 SGT |

- **Watchers** (`watch.py`): FSEvents on the folders, plus file watchers on the iMessage and WhatsApp databases. Both chat DBs are watched from the start, so a chat picked later needs no restart.

### One item's journey (live phase)

```
watcher / poll
  → discover   source.discover(cursor) → upsert items → enqueue prep for new/changed
  → prep       (thread) load text → scrub → chunk → embed (EmbeddingGemma, on Mac)
               → write chunks + vectors + FTS → near-duplicate check (0.97 cos: kept searchable, never compiled)
               → claude_mem / images / tiny files / secret files: done here, no LLM
  → triage     Jev on the NEW content only (last mail message, new chat lines): keep? which area? card wanted?
               → kept items become 'triaged', a wanted card is parked as meta.pending_card
  → live_compile (every 5 min) Haiku compiles triaged items in batches (≤8 small items or ≤6k tokens; big ones alone)
               → NoteDraft: title, summary, key facts, people, projects, topics, area hint, dates, `do` (card text)
               → Markdown note in the wiki (code writes it, never the model)
  → embed_note → resolve (registry, section 9) → summarise (entity pages) → render pages
  → a pending card + a `do` line = a suggestion card (section 10)
```

**Build phase vs live phase.**

- The first build (`setup.start_build` / `misty build`) runs discover → prep → Jev `worth` gate → compile on everything, under the $3 build cap.
- Then stage 5 runs: life areas on Opus, the about card, index, a priorities draft.
- After that, `phase` is `live` and only new content flows through the pipeline above.

---

## 8. Search and context packs

- **Embeddings** (`embed.py`): EmbeddingGemma 2 (bf16, MLX, pinned revision), 768-d.
  - Text and images share one space, so a text query can find a photo.
  - Queries are prefixed `task: search result | query:`, documents `title: none | text:`.
  - Shapes are padded to multiples of 64 and the MLX cache is capped at 512 MB. Without that, calls slowed from 40 ms to 112 ms over 3,000 calls.
  - The model loads once and is warmed at engine start.
- **Chunking** (`chunk.py`): packs paragraphs into chunks. Sentences split only when a paragraph is too long.
- **Index** (`index.py`): FTS5 (porter, unicode61, diacritics removed) and sqlite-vec, kept in step.
- **Hybrid search** (`search.py`):
  1. embed the query → vector KNN (200)
  2. FTS5 keyword (200)
  3. merge with **reciprocal rank fusion** (k=60, ties ordered by key so results are deterministic)
  4. filter by source (`files`, `photos`, `mail`, `calendar`, `imessage`, `whatsapp`, `claude_mem`) and time (`today`, `week`, `month`, `year`, `older`)
  5. `exclude_private` drops private chats (always on for anything a model sees)
- **Context packs** (`wiki/context.py`, `context()` v2): what any AI gets about a topic, within a token budget.
  - the about card
  - the entities that match, one entry per entity whatever the spelling, with merges followed
  - a hop to related entities
  - "open now" items
  - deterministic output
  - nothing from a private chat
  - Chat uses `_notes()` (300 tokens a message). Stream uses `chat.context_pack()` (matching facts first, then 350 tokens of wiki).

---

## 9. Registry and wiki

**Registry** (`registry/`): people, projects and topics are **rows**. Pages are only renderings of those rows.

- Ids are permanent (`en_` + 12 hex). A merged entity keeps its row and points at its survivor, so every merge can be undone (`entity.unmerge`).
- **Resolution** (`resolve.py`, stages R0 to R5) is pure code. For each mention it answers resolved / new / judge / plain / rejected, using:
  - names and handles (email, phone, WhatsApp JID)
  - the 1:1 chat counterpart
  - which entities were already in the same note
- **The sweep** (`sweep.py`) resolves a whole batch against the registry as it stood when the sweep began:
  - so arrival order can't matter
  - it's idempotent and never loses a mention
  - stage R6 is embedding similarity, with thresholds calibrated on real names: auto-fold at cos ≥ 0.92 with shared words (Jaccard ≥ 0.5), ask the judge at ≥ 0.80
  - stage R7 is a Haiku **judge** for what stays ambiguous
- An entity needs **2+ public sources** before it gets a page.
- **Summaries** (`wiki/summaries.py`) are the only thing that turns a candidate into an active page.
  - They're incremental: refreshed when enough new sources arrive, after a merge, or after a correction.
  - They're budgeted per day.
  - Prompts carry wiki text only (`WikiText` type), never raw files.
- **Life areas** (`wiki/areas.py`): Opus reads one compact digest of the whole corpus and says how *Isaac* divides his life. It's not clustering. Areas start as `proposed`, and Isaac accepts, renames or drops them.
- **Graph** (`wiki/graph.py`):
  - Me at the centre, areas as ring 1, the top 5 entities of each area (under 60 nodes)
  - expand an area to see its top 12
  - three edge kinds: belongs, involves (2+ shared notes), related (3+)
  - drawn with force-graph 1.52.0 (sha-pinned) in a web view
- **Priorities** (`wiki/priorities.py`): `priorities.md` with `- text <!-- id:… -->` lines under `## Priorities` (draft or approved), and `## From class` checkboxes (section 15).

---

## 10. Suggestions, prep and Priorities

- **A card is born** when triage says a card is wanted (`kind`, `urgency`: now / today / this_week) **and** Haiku's compile wrote a one-line `do` for that item.
  - No `do` means no card.
  - Old chat weeks from the backfill never make cards.
  - Cards are max 140 characters, grouped Now / Today / This week / Later, and snoozes release every minute.
- **Prep** (`prep.py`, every 60 s): plans each new card in the background so **Go** just runs it:
  - `calendar`: an event made in the Calendar app
  - `reminder`: a reminder in Reminders
  - `draft`: a reply put in the WhatsApp or Messages box, or a Gmail draft. **Never sent.**
  - `stream`: a short Stream task (open the doc, open the form)
  - `none`: she can't (it needs his login, money, thinking or another machine), and the card says why
- Background planning respects the paid lock. **Go** is Isaac's own say-so, so it plans and runs inside `user_approved()`.
- RPCs: `suggestions.list`, `suggestions.prep`, `suggestions.go`, `suggestions.act` (done, dismiss, snooze), with progress pushed as `suggestions.progress`.

---

## 11. Chat

`chat.py` + `api_chat.py`. One thread. Haiku 5.5 answers, with replies streaming.

### What the model sees each message

```
system[0] (cached):   SYSTEM rules (+ VOICE rules if spoken) · today (SGT) · about card (≤1200 chars) · facts list
system[1] (changes):  running summary of older messages · notes picked for this message (≤300 tokens)
history:              the newest unsummarised messages, each assistant turn WITH its real tool calls + results
user:                 the new message
```

- **Facts:** all active facts (up to 80). Past that, the 25 closest to the message.
- **Replay keeps tool calls.** Replaying replies alone ("Opened your essay") taught the model to claim actions without calling the tool.
- **Compaction:** `KEEP = 12` messages go word for word. Once 10 more pile up, the oldest fold into the running summary (`chat_compact`).

### Tools (12)

- **`remember` / `forget`:** facts. A key within 0.86 cosine of an existing key counts as the same fact. `attach_front` saves the doc or page open in the app he was last in.
- **`open_fact`:** opens the saved link or file. There's a fast path: `facts.wants_open()` spots "open my English work" and opens it before the model even runs.
- **`recall`:** hybrid search of his notes.
- **`search` / `read_page`:** the web.
- **`do`:** a Stream run.
- **`look`:** a screenshot of his front window. It's sent as an image message and never saved. Because the picture is gone after that turn, the saved call says so ("the picture is gone now; call look again to see it"), and the rules tell the model to open a school-question reply with the question itself in one line. A follow-up ("simpler", "why") then works from the chat, and a new question means a fresh `look`. Before this, the thread kept only "looked at his screen: Word", so the next message lost the question (reproduced in `test_follow_up_after_a_look_still_knows_the_question_and_that_the_picture_is_gone`).
- **`draw`:** opens the drawing window (below). The scene is the tool's argument, so a follow-up ("now shift demand right") is one new `draw` call and the old scene is already in the replayed history.
- **`calc`:** an AST evaluator. No `eval`. It takes optional variables (the drawing window passes `x`). It allows numbers, + − × ÷ // % **, brackets, sqrt, trig (degrees, or `…r` for radians), log, ln, abs, pi, e, with size limits.
- **`class_notes` / `class_search`:** Class Notes.

### The drawing window (`draw.py`, `Core/Draw/*`, `Views/Draw/*`)

Isaac's ask (24 Oct): a window of Misty's own where she draws graphs, tables and working while she explains, without Desmos.

- **Flow:** the model calls `draw(scene)` -> `draw.build` checks it and samples every curve (220 points, safe evaluator, `x` as the variable, "3x/(x-2)" and "x^2" accepted) -> `ctx.notify("draw.scene")` -> `RpcEvent.drawScene` -> `DrawVM.show` + `DrawPanel` (a floating `NSPanel` that opens beside his document without taking the keyboard). The tool result to the model is only `{ok, steps}`; the points never go back to it.
- **Why the engine samples:** the maths stays in Python where it is tested, curves break at asymptotes (a jump of more than 2.5 view heights, or a non-existent value, ends the piece), and Swift only draws polylines. A bad scene returns a short reason (`steps must be 1 to 8 items`) so the model fixes it in the next round.
- **Scene:** `{title, steps:[{say, do:[...]}]}`, at most 8 steps and 14 things a step; later steps keep what earlier ones drew. Things: `axes` (sets the view), `curve`, `vline`, `hline`, `dot`, `seg` (optional arrow), `text`, `poly` (filled area), `eq` (algebra lines, one highlighted in lime), `table`. Colours: ink blue red green orange purple pink gray. Axes ranges are `xr`/`yr` in the output, apart from a line's `x`.
- **Drawing:** one SwiftUI `Canvas` inside a `TimelineView` (40 fps while animating, paused 2.6 s after a step lands). Curves draw on, lines slide out, dots pop with a small overshoot, labels fade in. `DrawPainter` is pure (scene, step, start times, clock -> picture), so it renders to a PNG in tests (`DrawSnapshotTests`, set `TEST_RUNNER_DRAW_SNAP_DIR`). Equations and tables sit in a card beside the plot.
- **Pace:** a typed question plays itself, one step every 3.4 s (`DrawVM.stepSeconds`); Pause, Back, Next and Again hand it to Isaac. A soft tick on each step unless a class is open.
- **Spoken captions (tap-to-talk only, never in class).** When the scene arrives while a tap-to-talk answer is under way (`VoiceVM.canNarrate`: talking, a turn open, not in class), `Narrator` takes over the pace: show step 1, say its caption, wait until the speaker finishes it (`spokeAll` hands that to the narrator), rest 0.25 s, next step. Captions go through `Speakable.text` first ("3x^2 + 2 = 14" -> "3x squared plus 2 equals 14", "f(x) = 3x/(x-2)" -> "f of x equals 3x over x minus 2"). The chat reply's sentences that arrive meanwhile are held (`say` -> `narrator.hold`) and said after the last caption, because the speaker is one queue; the VOICE rules tell the model to write captions as spoken sentences and to reply with one short sentence after drawing. A tap (`interrupt`) or `end()` cancels it and drops the held reply; with no voice model it just steps on a timer. Typed chat and class never narrate (`say` also refuses in class).
- **Cost:** one tool call of a few hundred tokens. Live check: "show me f(x) = 3x/(x-2) with its asymptotes" took 1 round trip pair for $0.0014.
- **Not built yet:** LaTeX-quality maths (plain "x²" text for now), saving or exporting a drawing, the model seeing its own picture.

### Limits and flow

- Up to 4 tool rounds, then it must answer. Max $0.02 a message, 700 output tokens (300 for voice).
- **Voice style:** 1 to 3 short spoken sentences, no lists or markdown, numbers said as words, "one step at a time" when helping with a question.
- **Stop** (`chat.stop {turn}`): the delta callback raises `Stopped`, and `_unless_stopped` also cuts off a model that's still thinking or writing a tool call. The partial reply is saved with `{"stopped": true}`.
- **Confirm:** a risky `do` step sends `chat.confirm`. The app shows Yes/No (or voice answers it), then `chat.answer` resumes.
- **Cost:** about $0.00065 for a 5.3k-token message (0.07¢).

---

## 12. Stream: hands on the Mac

One run is a loop (`stream/loop.py`):

```
observe (Swift reads the app's Accessibility tree) → brain (one `act` tool call) → gate → act (Swift does it)
→ see what changed → log → repeat until `done`, `ask_user`, or a limit
```

- **Limits:** 20 steps and $0.10 a run.
  - An action that changed nothing is refused after 2 tries.
  - 6 steps in a row with no change ends the run.
  - The same action 3 times counts as going in circles.
- **Observe** (`observe.py`):
  - The Swift `SnapshotWalker` returns up to 160 nodes, and the brain sees up to 120 as a numbered text list (`[12] button "Send"`).
  - Long lists show 6 rows. Values are cut at 80 characters.
  - Plus a diff of what changed since last time.
  - Numbers are only valid for that observation. The brain never picks pixels.
- **Brain** (`brain.py`): any model listed in `[stream.brains.*]` (Haiku by default). It gets text, plus a screenshot only on `look`. It goes through the same one cloud door.
- **Actions** (`actions.py`):
  - `press`, `type_into` (sets a field directly; `submit` presses return; text areas append), `type_keys`
  - `menu`, `shortcut`, `open_app`, `open_url`, `scroll`, `look`, `wait`
  - `list_files` (newest files in a folder, beats sorting Finder by hand), `open_path`
  - `clear_desktop` (minimises every window except Misty's), `ask_user`, `done`
  - Typing into SwiftUI fields and launchers is done by **pasting**, because synthetic key events don't reach them.
- **App Cards** (`appcards.py`): built from the app's own menu bar (every item and its shortcut), plus hand-written tips in `stream/cards/<bundle id>.toml`. Cached for a week.
- **Bridge** (`bridge.py`):
  - The engine sends `hands.request {req_id, op, args}`, and the app answers with `hands.result`.
  - The app also reports Isaac's own keys and mouse (`hands.user_input`, via an Input Monitoring tap). Misty's own events are tagged, so a take-over can be spotted.
- **Swift side:**
  - `SystemHands`: AX for reading and pressing, CGEvent for keys and mouse, refs valid until the next tree
  - `Capture`: CGWindowList plus the system `screencapture` tool for one window, because ScreenCaptureKit's `captureImage` hung inside Misty. Saved to a private temp file, read, then deleted.
  - `HandsService` checks permissions first, so missing grants come back as plain codes (`NOT_TRUSTED`, `SCREEN_RECORDING_DENIED`).
- **StreamCursor:** a click-through overlay window with a teal pointer that glides along a `CursorPath`, pulses on press, and shows what it's pressing or "Thinking". It's only a picture. The real mouse is untouched, because presses go through Accessibility.
- **Run logs:** `~/Misty/stream/runs/<id>.jsonl`, one line per event. They're meant as training data for Isaac's own model later, and stay local only.

---

## 13. Voice

All of this lives in the app (`app/Misty/Core/Voice/`). The engine only sees text.

### The audio graph (`AudioIO.swift`)

- **One `AVAudioEngine` for the mic and Misty's voice.** Her voice plays through an `AVAudioPlayerNode` on the same engine, so macOS voice processing (echo cancellation) knows exactly what she's saying and removes it from the mic.
- **Voice processing on, auto-gain off.** AGC was lifting distant room talk and her echo up to the level of Isaac at the keyboard. Other apps' audio isn't ducked.
- With voice processing on, the input arrives as about 9 identical channels on a MacBook, and `AVAudioConverter`'s downmix of that comes out silent. So it takes channel 0 itself, then resamples to **16 kHz mono Float32**.
- `prepare()` switches voice processing on ahead of time, because the first switch costs about a second.
- **Side effect:** echo cancellation also removes **everything else the Mac plays**, which includes a Zoom teacher (section 15).

### The endpointer (`Endpointer.swift`)

Voice activity detection by loudness against a moving noise floor, in 20 ms frames.

| Setting | Close talk (default) | Class (`classTuned`) | While Misty speaks |
|---|---|---|---|
| speech starts after | 120 ms loud | same | same |
| ends after quiet | 650 ms | 600 ms (100 ms gap after 6 s) | same |
| min level (RMS) | 0.0015 | 0.0008 | 0.012 |
| over noise floor | ×3.0 (~10 dB) | ×2.2 | must also beat the loudest echo of the last second ×1.6 |

- It keeps about 500 ms of pre-roll, so the first word isn't clipped.
- A phrase is cut at **14.5 s**. The model refuses anything over 15.0 s, and padding a 15.0 s phrase makes it 15.02 s. The log on 2026-10-23 09:34 shows exactly that error before the cut was fixed.

### The ears (`Ears.swift` + `Vendor/phonon-coreml`)

- **Phonon-2** (Fermion Research): a 164 MB quantised copy of NVIDIA Parakeet TDT 0.6B v3.
  - **English only.** The teacher model does 25 languages, but this copy is tuned for English.
  - About 5.2% average word error on the Open ASR sets, the best open English model under 900 MB.
  - It runs as Core ML on the **Neural Engine** (`cpuAndNeuralEngine`), with one encoder size loaded (15 s, about 115 MB; all four sizes were 660 MB).
- **It's not a streaming model.** Misty fakes streaming:
  - While you talk, the whole phrase so far is **re-read** every 0.8 s (0.4 s while Misty speaks, so a "stop" lands fast).
  - When the endpointer closes the phrase, the whole thing is read once more for the **final** text.
  - A read of a few seconds of audio takes about 0.1 to 0.2 s.
- **Extra rules:**
  - The same partial 3 reads in a row means the phrase is done, even if the room stays loud.
  - 4 reads in a row with no words means noise, so the phrase is dropped.
  - A final with no letters (a cough, a door) closes silently.
  - A session counter stops a late read leaking into a new session.
- **Warm-up:** loading the model and doing one silent read costs **60 to 130 s on most launches** and 2 to 5 s on a few (section 18).
- The model lives in `~/Library/Application Support/Misty/Phonon-2-CoreML/` (fetched by `scripts/fetch_ears_model.sh`). Its compiled form is cached under `~/Library/Caches/phonon-coreml/<key>/` at a stable path.

### The mouth (`Speaker.swift`, `SentenceSplitter.swift`)

- **Phonon TTS** (Gradium, `xn-ptts` package, the Phonon 31m Core ML model, Apache 2.0, 24 kHz). Models are fetched by `fetch_voice_models.sh` and copied once to Application Support, because Core ML compiles them there.
- **The splitter** cuts streaming text on `. ! ?` and newlines, and on a comma once a piece is over about 120 characters. So she starts speaking before the reply is finished.
- Sentences queue and play back to back. `stopNow()` drops scheduled audio and cancels generation, going silent in well under 100 ms.
- She's warmed at launch with one silent utterance (2 to 15 s).

### Summon (`SummonKey.swift`)

- `ChordDetector` is a pure state machine: Control and Option both down, nothing else pressed, both up within 600 ms.
- Ctrl+Space (his Siri), Ctrl+Option+key shortcuts and Option alone (his Whisper key) never trigger it. A tap toggles voice or live mode.

### VoiceVM states and rules

- **States:** `idle → starting → listening → thinking → speaking`.
- **`VoiceIntent.of()`** sorts what was heard **before** the brain sees it:
  - **hush:** "stop", "shut up", "wait", "hold on", "be quiet", "pause", "enough"…
  - **bye:** "bye", "that's all", "thanks Misty"… In live mode this is routed like anything else, because "bye" is usually said to someone else.
  - **yes / no:** only while a spoken confirm is open (4 words max).
  - **say:** everything else.
- **Echo:**
  - A heard phrase where 60%+ of the words are ones she just said is her echo, and it's ignored.
  - That only applies while she's speaking or within 2 s of her finishing (`echoTail`).
- **Barge-in:**
  - **While she's speaking:** `isBargeIn` (a stop word or her name, and not echo) **or** `isTalkOver` (2+ real non-filler words, not echo) cuts her off. The speaker stops, the turn gets `chat.stop`, and the state goes to listening.
  - **While she's thinking:** only `isBargeIn`.
  - A tap on the hotkey also cuts her off.
  - `barge_stop_ms` is logged.
- **Turn flow:**
  - `send()` → `chat.send(text, voice: true, turn:)`
  - `chat.delta` events for that turn → splitter → speaker
  - `VOICE_TURN` timings logged: `begin_ms`, `first_audio_ms` (about 1.8 s after you stop talking), `barge_stop_ms`
- **VoicePill:** a non-activating panel at top centre showing the state word and the live words or her current sentence.

---

## 14. Live mode

**The goal (Isaac's words):** talk to her like ChatGPT voice, with no wake word, while she ignores talk that isn't for her.

**How it works now:**

- The mic stays on after the hotkey, with a one-time consent sheet (`misty.liveConsent.v1`).
- Every final phrase goes through `VoiceVM.route()`:

```
heard final
 ├─ no real words, or a lone "yeah/ok" (room noise)           → drop
 ├─ "Misty, class mode (off)"                                  → toggle class mode
 ├─ class mode on                                              → drop (silent)
 ├─ she's thinking, hasn't spoken, sent < 6 s ago (mergeWindow) → stop that turn, resend old + new as ONE message
 ├─ a held cut-off sentence is waiting                         → join and deliver
 ├─ says "Misty"/"Missy", OR follow-up (< 8 s after her reply)  → deliver
 └─ otherwise → live.hear (Jev) → to=misty? → finished? → deliver : hold
                                 → anything else ("someone_else", "nobody", "unsure") → stay quiet
deliver: looks cut off ("…about the", "and", trailing comma)? hold up to ~4 s (1.6 s after he goes quiet)
         she's busy? queue it (never cancels her current reply) : send
```

- **`live.hear`** (`hearing.py`) sends one Jev call with three questions:
  - **to:** misty / someone_else / nobody (with hint text for each)
  - **finished:** yes/no
  - **todo:** yes/no
  - Context sent: the phrase, plus what Misty last said if that was within 120 s.
- **Thresholds:** `to` must have confidence ≥ **0.6** (`SURE`), or it becomes `unsure` and she stays quiet. "Cut off" needs ≥ 0.9, because a wrong "cut off" makes her wait seconds on a finished question.
- **Overheard to-dos:** if the phrase isn't for Misty but has a to-do (confidence ≥ 0.6), it's saved as a fact `heard: <first 6 words>` ("Overheard Fri 23 Oct 18:50 (to-do for Isaac): …"). Isaac can see and delete these. Nothing else of other people's talk is kept.
- **Cost:** about $0.000033 per phrase, capped at $0.05 a day. Past the cap, only phrases with her name reach her.

**Why it fails today (from the 2026-10-23 logs):** Jev's confidence on clearly addressed questions was 0.10 to 0.34:

| Phrase | Jev confidence |
|---|---|
| "Dude, can you tell me what's five times five?" | 0.34 |
| "Uh what's five times five?" | 0.16 |
| "Oh, why is it at seventy four cents?" | 0.10 |

So without her name she almost never answers. See section 18 for the plan.

---

## 15. Class Notes

Granola-style: a live transcript during class, full notes after.

### During class (`ClassVM.swift`, `api_class.py`, `classes.py`)

- **Start** (button, Classes tab, or "Misty, class mode"):
  - `class.start {subject}` creates a session titled like "Chem, 23 Oct"
  - VoiceVM turns the ears on in **class mode**: `classTuned()` endpointer, and nothing goes to chat or Jev
  - the floating `ClassPanel` opens (an `NSPanel` at the right edge, above Zoom, on all Spaces)
- **Each final phrase** becomes `class.segment {session, t_start, t_end, source:"room", text}`, timed from the class start.
  - `t_start` comes from the first partial, or is estimated at 0.4 s a word.
  - Partials show greyed in the Transcript tab.
- **Notes tab:** his typing, saved 1 s after he stops. Each new line gets a `[mm:ss]` class time.
- **Ask box:** `class.ask`. It does an FTS search of the segments (plus one segment either side), then Haiku answers in 1 to 4 sentences with JSON `{answer, cites}`. The cites show as time chips. With no class open, it asks across classes (filterable by subject).
- **Lid closed:** the system sleep notification pauses the class with a "lid" gap, and wake resumes it. 15 quiet minutes ends it. A class the app left open gets ended after 30 quiet minutes at the next engine start.
- **End:** the last phrase in flight is flushed (`ears.flush()`), the notes are saved, `class.end` runs, and the panel shows that class's notes as they're made.

### After class (`class_notes.py`)

1. If there's too little (under 150 words), the status is `too_little`.
2. **Spend cap first:** if the class has already cost $0.05 or more (all tries, across restarts and Regenerate), it fails without calling.
3. One call on **Haiku 5.5** (switched from Opus on 2026-10-23, about $0.003 a 40-minute class), with `max_tokens` scaling as `1500 + 8 × segments`, clamped to 2,500 to 8,000.
4. The prompt sends numbered segments (`[id] mm:ss text`), his typed notes with times, the gaps, and the class date. It asks for JSON:
   - `summary`
   - `sections[{heading, points[{text, seg[ids]}]}]`
   - `remember[{kind: definition|formula|exam_hint, text, seg}]`
   - `action_items[{text, kind, due, due_text, seg}]`
5. **Retries:**
   - Network blips get 3 tries (waiting 2 s then 8 s).
   - A reply that isn't JSON gets **one** more try. If it was cut off (`finish_reason=length`), the retry gets double the room, up to 12,000.
   - Every try's cost is added to the class.
6. **Checking against the transcript:**
   - Every point, remember item and action item must cite real segment ids, or it's dropped.
   - His typed lines go in word for word as `mine: true` points, never reworded.
   - Typed lines that weren't placed go under "Your other notes".
7. **Due dates** resolve in SGT from the teacher's words (`resolve_due`): "next Tuesday", "tomorrow", "in 3 days", "28 Oct", ISO dates.
8. Remember items go into the wiki. Action items go to `class_actions` **and** `priorities.md` under `## From class` (`- [ ] Chem: Q1-8 p112 <!-- id:… area:chem due:2026-10-27 cls:… -->`). Ticking one in either place syncs both. Lines done or overdue for more than 7 days are cleared.
9. The `class.notes` event refreshes the panel and the Classes tab.

**Retention:** transcripts are kept 6 months (`KEEP_S = 183 days`), notes forever. After 6 months, Ask answers from the notes.

**Limits today:** only the mic is heard (the room or Isaac). A Zoom teacher coming out of the Mac's speakers is removed by echo cancellation. There's no Me/Teacher labelling, and Spanish comes out as Spanglish (parked).

---

## 16. The Swift app

- **`AppState`** (`@MainActor @Observable`) owns:
  - the supervisor and RPC client (`LiveAPI`)
  - the view models (`SuggestionsVM`, `PrioritiesVM`, `ChatVM`, `PermissionsVM`, `VoiceVM`, `ClassVM`)
  - the hands bridge and input tap, the summon key, and an `NWPathMonitor` for online state
  - It fans out engine notifications: `chat.delta`, `chat.status` and `chat.confirm` go to ChatVM, and to VoiceVM when the turn is a voice turn. `hands.request` goes to HandsService, and so on.
- **Tabs** (`RootView`): Chat (⌘1), Suggestions (⌘2), Priorities (⌘3), Memory/graph (⌘4), Classes (⌘5), Settings (⌘,). There's also a menu bar popover (`MenuContent`) and a status strip (engine state, build, spend).
- **Setup flow** (`Setup/`):
  - OpenRouter key (tested with a $0.00002 call)
  - Google OAuth (import a client, then the auth flow)
  - Full Disk Access check
  - WhatsApp chat picker and iMessage exclusions
  - a Gmail size estimate, then start the build
- **Permissions page:** Accessibility, Screen Recording, Microphone, Input Monitoring, Full Disk Access, each shown as Allowed or Blocked, with a button to the right System Settings pane. macOS asks once per permission, and Screen Recording can need a restart.
- **Settings:**
  - Voice: hotkey, Class mode (silent), Speak replies, voice picker
  - Spending caps (daily, first build)
  - Sources
  - Pause everything, Launch at login
  - Delete all Misty data (type DELETE: removes `~/Misty` and every Misty Keychain item, and leaves his own files untouched)
- **Look:** currently "quiet-pro" (dark graphite, one teal accent, `Theme.swift`). Isaac rejected it, and the redesign is parked (`PRODUCT.md` has the new direction).

---

## 17. Build, run, test, debug

| Do | How |
|---|---|
| Engine tests | `cd engine && .venv/bin/python -m pytest ../tests/engine -q` (935, about 2 min) |
| Swift tests | `bash scripts/swift_test.sh` (162) |
| Build the dev app | `bash scripts/dev_app.sh` → fetches models, `xcodegen` + `xcodebuild` (Debug) → copies to `build/dev/Misty.app`. Same path and same signing identity, so macOS keeps the permission grants |
| Bundle the engine | Xcode post-build `scripts/bundle_engine.sh`: a standalone Python + engine + deps into `Contents/Resources/engine`, skipped when the source hash is unchanged |
| Engine CLI | from `engine/`: `uv run misty acceptance`, `registry rebuild --dry-run`, `spend --by-purpose`, `paid unlock|lock`, `eval --suite answers` |
| Logs | `~/Misty/logs/engine.log` (`OR_CALL`, `RUN`, `HEAR`, `CHAT_TURN`, `JOB_FAIL`, `CLASS_NOTES_*`…), `~/Misty/logs/voice.log` (`warm`, `heard partial/final`, `phrase`, `hear to=`, `VOICE_TURN`) |
| Look at data | `sqlite3 ~/Misty/misty.db` |

**Env switches:**

| Variable | Does |
|---|---|
| `MISTY_HOME` | data folder (default `~/Misty`) |
| `MISTY_ENGINE_CMD` | run the engine from the repo instead of the bundle |
| `MISTY_CONFIG` | another `pinned.toml` |
| `MISTY_OPENROUTER_BASE` | fake server for tests |
| `MISTY_EARS_MODEL` | ears model folder |
| `MISTY_PHONON_MODELS` | TTS models |
| `MISTY_VOICE_DEBUG=1` | log modifiers + mic level every second |
| `MISTY_EARS_DUMP=<dir>` | save phrases as wav (debug only) |
| `MISTY_CLASS_AUTOSTART=<subject>` + `MISTY_CLASS_WAV=<file>` | the silent class test: feeds a wav through the class path, nothing played aloud |

**House rules for changing Misty:**

- Reproduce a bug before patching it.
- Test-first for engine logic.
- Never add a second path to the cloud.
- Never log content.
- Commit with the `Co-Authored-By` line.
- Ask Isaac before rebuilding or restarting while he's testing, and before pushing.

---

## 18. Current state and known issues

As of **2026-10-23**. Update this section first when things change.

### Live right now

- **Branch `stream`, nothing pushed.** The last commits are `f398d4c` (class notes stop paying for cut-off replies) and `5125367` (class notes on Haiku 5.5, 5¢ cap).
- **The running app is an older build.** Those two fixes take effect after `scripts/dev_app.sh` and a restart.
- **`paused = true`** (Pause everything): the job worker claims nothing.
- **`paid_calls_allowed = false`**: background paid calls are locked. Only things Isaac triggers run (chat, Go, class notes, hearing).
- **So the memory hasn't updated since about 19 Oct.**
  - The last compile was on 19 Oct, and the last discover on 21 Oct.
  - 357 `summarise`, 40 `triage` and 7 `compile_batch` jobs failed with `PAID_LOCKED` on 18 and 19 Oct.
  - 33 `prep` and 13 `discover` jobs are queued.
  - Unpausing and unlocking would restart it: about 1 to 5¢ a day at normal volume. **That's Isaac's call.**

### Added 24 Oct (evening)

- **Calendar is Google, live.** New chat tool `calendar` (`calendar_now.py`): reads every Google calendar Isaac has switched on, for today, tomorrow or a date plus up to 14 days, with meeting links. Before this Misty had no schedule tool, answered from stale notes, then offered "want me to open your calendar" and Stream opened Apple Calendar. New `open_url` tool (https only) joins a class from the calendar link; `open_fact` now also finds a link written inside the fact's sentence (the "physics class link" fact had its Meet URL only in the text).
- **Act, don't offer.** The system prompt now says: read-only next steps are done in the same turn, no "Want me to...?" endings; ask only for missing info, real forks, or risky steps (send, delete, buy, post). Measured on the 180 saved replies: 22% ended in a question, 13 as offers.
- **Last word cut off.** `chat.send`'s answer and the streamed `chat.delta` events reach the app on different routes, so the full reply could arrive before the last piece. `ChatVM.send` now returns the reply; `VoiceVM.send` speaks whatever the stream had not delivered (`ReplyTail.missing`), ignores late stream pieces after that, and the chat bubble takes the full reply too. Logged as `reply tail recovered`.
- **Speech speed and voice test.** Phonon has no speed setting, so `Speaker` runs her output through `AVAudioUnitTimePitch` (0.7x to 1.2x, pitch kept, default 0.9x). Settings > Voice has a Speed slider, a "Play test phrase" button, and picking a voice plays the phrase.
- **RAM: engine 2.7 GB to about 1.5 GB (GPU part 1.9 GB to 0.56 GB).** The EmbeddingGemma checkpoint carries a speech tower (610 MB) and a photo tower (335 MB) the text path never touches. `embed.py` drops both after load (text vectors measured bit-identical), reloads the photo tower only when a photo is embedded and drops it after 5 idle minutes, and hands back MLX scratch buffers after 20 idle seconds. Still there: torch + torchvision (260 MB) pulled in by mlx_vlm, only needed for photo preprocessing.
- **Cost report.** `spend.breakdown` RPC (`cost_report.py`): totals by job (plain names) and day, plus the newest replies with every model call (tokens in, cached, out, cost, tools asked for) and what a tool spent itself (Stream, class answers). Chat meta now stores `rounds` and `tool_usd`. App: Settings > Spending caps > "See where the money goes...".
- **Crash fix: stale event-tap callbacks.** `UserInputTap` and `KeyTap` handed the callback a raw pointer; a callback already queued when `stop()` ran (engine restart, test host) jumped through freed memory (EXC_BAD_ACCESS, PC 0, in `seen`). The pointer is now a token looked up in `TapOwners`, removed first thing in `stop()`. `UserInputTap` also re-enables itself when macOS switches it off, like the other taps.

### Class view: online vs in person (24 Oct, late)
- **Online class = the Mac's sound is the teacher.** `SystemAudio.swift` is a Core Audio process tap on the default output (macOS 14.2+; asks once for System Audio Recording, `NSAudioCaptureUsageDescription`). It sees whatever the Mac plays, so Zoom, Meet, Teams, Zoho or a whiteboard app all work with no integration. Misty's own sound is excluded, the class keeps playing for him (`muteBehavior = unmuted`), and a change of output device (AirPods) rebuilds the tap.
- **Two ear streams, one model.** `Ears(sharing:)` gives the Mac's stream its own endpointer but the same loaded Phonon-2 model and read queue, so memory stays flat and the two never read at once.
- **Labels.** Segments keep `source`: `mac` = the Mac's sound (shown as Teacher), `room` = the mic (shown as Me in an online class, unlabelled in person). `class_sessions.mode` (`room`|`online`, migration v9) is set at `class.start`; the notes model sees `Teacher:` / `Isaac:` prefixes and the format line only for online classes.
- **Speaker echo.** Without headphones the mic hears the teacher too. A mic phrase of 3+ words that is at least 80% words the Mac's stream said in the last 12 s is dropped (`VoiceIntent.isTeacherEcho`); short phrases and questions that only reuse her terms stay.
- **Which mode.** `class.now` asks Google Calendar what he is in (from 10 min before to the end). Online when the event has a video link (Google field, or a Zoom/Meet/Teams/Zoho/Webex URL in its location or description), or an active saved fact for that class holds a meeting link or says the class is online. The title picks the subject. Start class shows an Online / In person switch with that as its default and the reason underneath.
- **No sound warning.** If the tap has delivered only silence for 20 s, the class panel shows a line pointing at System Audio Recording (macOS gives zeros when it is off; a muted teacher looks the same).
- **Shared screen (online classes).** `SlideWatcher` looks at the front meeting window every 10 s while the class is live: Zoom, Teams, Webex or Zoho windows, or a browser window whose title says Meet / Zoom / Teams / Webex / Zoho / classroom / lecture / webinar (needs Screen Recording to read titles), at least 400x250, never Misty. It grabs the window with `/usr/sbin/screencapture -l` to a private temp file that is deleted at once (ScreenCaptureKit hung inside Misty before), compares a 32x18 grayscale thumbnail and only reads the picture when it changed, then runs Apple Vision OCR on the Mac, drops the call's own buttons ("Mute", "Chat"...) and posts the text as `class.slide {session, t, text}`. A slide counts as new when it has at least 4 words and under 70% overlap with the last one. No picture is kept and no model is called. `class_slides` (migration v10) holds the text, max 120 a class, 1200 characters each, deleted with the class; the notes prompt gets at most 30 spread across the class at 400 characters each, under "On the shared screen". In person, nothing looks at the screen.
- Not built yet: voice separation (diarisation) for a room.

### Fixed 24 Oct

- **Test phrase played on opening Settings, on any sidebar click, and on every voice or speed change (in an online class).** Root cause: `.onAppear { picked = ... }` set the `@State`, which fired `.onChange(of: picked)`, which called `speakSample()`; the slider's `editing` callback did the same. The picker and slider now use get/set bindings, so changing them is silent, and only the "Play test phrase" button plays it. `VoiceVM.speakSample()` and `say()` refuse while a class is on or open (`inClass`), and the button is disabled and says so (`SamplePhraseTests`).
- **Follow-up forgot the question on screen.** See `look` above. Maths answers are also told to be short: question in one line, at most 4 steps, answer on its own line, jargon only when the exam needs it, "simpler" keeps the same question.

- **"323 failed" in the strip.** Gmail and Calendar checks failed with `NEEDS_AUTH` every minute and nothing recorded it, so the scheduler queued two more each minute. `discover` now sets the source to `needs_auth` (the app shows "Reconnect Google"), the scheduler stops polling it, and signing in again clears it. `NEEDS_AUTH` and `SUPERSEDED` failures no longer count in the strip.
- **Retry crashed** (`IntegrityError` on the one-active-job-per-item rule) when the same item had failed twice. It now requeues only the newest, marks the older ones `SUPERSEDED`, and skips locked or signed-out jobs.
- **Full Disk Access needed a restart.** iMessage and WhatsApp were only checked at engine start. The scheduler now rechecks every 30 s while they are `needs_fda`.
- **Other apps' volume dropped while Misty was open.** Voice processing was switched on at launch and never off, and macOS ducks everything else while it is on (about 4 dB, 15 dB for a moment at start). Now it is on only while live mode runs, off again when it ends, and dictation records the plain mic. Test hook: `MISTY_LIVE_TEST=<seconds>`.
- **Banners doubled and "Reconnect" did nothing.** One banner per problem now (Messages and WhatsApp share Full Disk Access, Mail and Calendar share Google). The Reconnect button was an empty `break`; it now runs `setup.google_auth_start` with a 300 s wait, shows "Waiting for Google…" and says why if it fails. The engine's sign-in server gives up at 280 s.
- **Dictation word fixes.** Phonon-2 is Parakeet TDT 0.6B v3 with the encoder squeezed to five-value weights and greedy decoding with no language model, so names it has never seen come out as real words ("of sorts" as "of socks", Groq as Grok). `DictationText` fixes a short built-in list after the read, plus his own `~/Misty/dictionary.txt` (`heard => wanted`, one per line). Offline tests with `say` voices: clean speech 1-2% word errors, pink noise down to 3 dB 2-10%, a 6x hot mic or hard clipping no change, 33 s clips through the 15 s windows no change, so the gap he hears is real-voice accent and rare words, not the mic path. Next step if it persists: replay his real recordings against Apple's on-device speech and a Whisper large-v3-turbo.
- **Opt-in dictation samples.** While the file `~/Misty/dictation-keep` exists, every dictation is saved to `~/Misty/dictation-samples/` as a float32 wav (exactly what the model heard) plus a txt with what it heard and what was pasted. Newest 100 kept, local only. Delete the flag file to stop.

- **Voice is tap-to-talk now; live mode is parked (24 Oct, Isaac's call).** Tap Left Command+Option (Left Control+Option still works): the mic opens, tap again to send, the whole recording is read once by the ears, cleaned like dictation, and goes to chat as a voice turn; her reply is spoken sentence by sentence. A tap while she thinks or speaks cuts her off and starts listening again; a quick double tap cancels. Plain mic (no echo cancellation), so other apps' volume is untouched. The always-listening code (`begin`, `classify`, barge-in, merge) is still in `VoiceVM` for Class Notes and for bringing live mode back later; the hotkey no longer reaches it. Test hook: `MISTY_TALK_TEST=<seconds>` (tap, wait, tap, wait for her, tap to interrupt, tap to cancel).

- **Crash when she acted on the screen from Misty's own window.** The brain asked `front_app`, got Misty (she was in front), then `ax_snapshot` of her own pid; reading a SwiftUI window's accessibility tree from a background thread trips a main-actor assertion (`EXC_BREAKPOINT`, in `ChatView.body`). `HandsService` now refuses Misty's own pid (`IS_MISTY`) and `front_app` returns the app before Misty when she is in front. Flaky and unrelated: `RpcClientTests.testRequestResponseCorrelationOutOfOrder` assumes which concurrent call is sent first.

### Known issues, worst first

1. **Live mode doesn't answer without her name.**
   - Jev's "who is this for" confidence is too low on real speech (0.10 to 0.34 on clear questions).
   - **Plan:** replace the Jev judge with Haiku 5.5 (about the same price per phrase, around 300 tokens in), give it the last few heard lines and Misty's last reply, and make "being in a conversation with Misty" sticky. Then measure false answers against misses on a recorded set of phrases (text only).
2. **The reply answered the old topic.**
   - "Misty was five times five" (her name misheard as "was what's") got a reply about the earlier Rubik's cube record instead of 25.
   - The previous turns had a stopped `do` run and a long search reply in history.
   - Needs reproducing in a test with that history, then a fix (for example, the voice prompt telling her the newest message wins, or trimming stale tool-heavy turns in voice).
3. **The ears take 60 to 130 s to warm up on most launches** (see the `warm ears_ms=` lines in `voice.log`). This is the real bottleneck Isaac flagged.
   - **What we know:**
     - The vendor already compiles the `.mlpackage` once to a stable `.mlmodelc` path (`~/Library/Caches/phonon-coreml/<key>/`).
     - The remaining cost is the **Neural Engine's own compile** (e5rt/ANE), cached in `~/Library/Caches/com.isaacmiranda.Misty/com.apple.e5rt.e5bundlecache/`.
     - Slow launches **write new entries** there (new folders at 18:33 on 23 Oct), so the cache is being missed rather than read.
     - A few relaunches minutes apart were fast (2.4 s and 4.6 s), which points at a key or daemon cache that doesn't survive.
   - **Candidates:**
     - (a) find what changes in the ANE cache key per launch: config, function name, code signature, dev rebuild path
     - (b) start on `cpuAndGPU` (compiles in seconds), so live mode works at once, and switch to the Neural Engine in the background when it's ready
     - (c) keep a tiny helper process alive that holds the model across app restarts
   - (b) is the safe first fix whatever the root cause turns out to be.
4. **No true streaming speech to text.** Phrases are re-read every 0.8 s, and the final is read after 650 ms of quiet. That's fine for accuracy, but it adds latency. Gradbot-style ideas to borrow:
   - end-of-turn by meaning, not only silence
   - starting the reply on a confident partial
   - pushing micro-silence to flush the tail
5. **Barge-in is close but not ChatGPT-level.** While she's *thinking*, only stop words cut her off. Talk-over needs 2+ words, and echo on MacBook speakers can look like words.
6. **The Zoom teacher isn't heard, and there are no Me/Teacher labels.** Fix: capture system audio (ScreenCaptureKit audio, or a Core Audio process tap) as its own stream with `source:"system"` = Teacher, keep the mic as `source:"room"` = Me, and merge both on one timeline (the Granola approach).
7. **Phrase cap:** a nonstop phrase is cut at 14.5 s (the ears refuse anything over 15 s).
8. **Spanish:** parked (Phonon-2 is English-only). Parakeet v3 multilingual is too big for now.
9. **The look:** parked until the features feel right.
10. **The 2026-10-23 Spanish class** (`cs_d12d13f4`) has no notes. A Regenerate on the new build costs about 0.3¢.

### What's next (agreed with Isaac 2026-10-23)

1. **Live mode, very good:**
   - a better "is this for me" judge with conversation context
   - fix the wrong-topic answer
   - instant interrupts
   - fast ear warm-up
   - faster first audio
2. **Zoom audio + Me/Teacher labels.**
3. **Spanish** (later).

---

## 19. Glossary

| Word | Meaning here |
|---|---|
| **Engine** | the Python process that owns data and model calls |
| **Hands** | the Swift side of Stream: reading and pressing other apps |
| **Stream** | Misty's computer-use loop (observe, brain, gate, act) |
| **Jev** | TypeSafe's tiny judgement model: answers fixed questions with a choice and a confidence |
| **Haiku / Opus** | Claude Haiku 5.5 (the workhorse) and Opus 5.5 (rare, strong) |
| **ZDR** | zero data retention: the provider can't store, log or train on the request |
| **Paid lock** | `paid_calls_allowed`. Off means only calls Isaac triggers (`user_approved`) can spend |
| **Purpose** | the label on every paid call, used for sub-caps and the spend breakdown |
| **Item** | one thing read from a source (file, thread, chat-week, calendar-week) |
| **Chunk** | a piece of an item's scrubbed text, the unit of search |
| **Entity** | a person, project, topic or area row in the registry, with a page once it has a summary |
| **Context pack** | the small bundle of about card + relevant entities that any AI gets about a topic |
| **Card** | a suggestion. **Prep** = its background plan. **Go** = run it |
| **Fact** | a thing Isaac told her to remember, one per key |
| **Ears** | Phonon-2 speech to text on the Neural Engine |
| **Endpointer** | the loudness-based voice activity detector that decides when a phrase starts and ends |
| **Partial / final** | the running guess for the phrase in flight / its finished text |
| **Barge-in** | Isaac cutting her off while she's talking or thinking |
| **Live mode** | always-on mic, no wake word, she decides what's for her |
| **Class mode** | ears on, silent, everything goes to the class transcript |
| **Segment** | one transcript line of a class with its start/end time |
| **ANE / e5rt** | Apple Neural Engine and its runtime, which compiles models for the chip and caches the result |
