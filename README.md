# Misty

Part 1 of 4 of Misty, Isaac's Mac assistant. Turns files, mail, calendar, chats and AI memory into a linked,
LLM-compiled wiki plus fast hybrid search. Engine: Python 3.12 (uv). App: Swift menu bar app (M6).


## Hard rules
- Ground-up build. Written from scratch, no code from the author's earlier projects.
- Scrub secrets locally before any cloud call. Every OpenRouter request sets provider.zdr=true through ONE client.
- No content or prompts in logs. Keys and tokens only in the macOS Keychain.
- Online-first, no offline mode. Every model call goes through one API runner (OpenRouter, ZDR, per-purpose budgets). No Claude subscription path.
- Read-only on Isaac's data, except what he chose (16 Nov 2026): add a calendar event on his yes, make a Gmail draft (never send), send one iMessage on his yes, fill a WhatsApp chat for him to send, flip Focus through his own Shortcuts. Never edits his files. Google account: set `MISTY_GOOGLE_ACCOUNT` to yours (Misty refuses any other).

## Tests
    cd engine && uv run pytest ../tests/engine -q

## Engine (after the Part 1 quality pass)
- **One runner**: every model call goes through `ApiRunner` (OpenRouter, zero data retention, per-purpose sub-caps). No subscription or CLI path exists (a guard test fails if one appears).
- **Registry**: people, projects and topics are rows in `misty.db` (`entities`, aliases, handles, mentions, merges with undo, redirects). Pages are renderings of those rows and exist only with a summary. Resolution runs at write time (`registry/resolve.py`, judge in `registry/sweep.py`).
- **Reading it**: `context()` and `entities.lookup()` (see `docs/ARCHITECTURE.md`).
- **Commands** (from `engine/`): `uv run misty acceptance [--with-answers]`, `registry rebuild --dry-run|--apply --max-usd`, `wiki repair`, `wiki adopt-legacy`, `onboard summarise --max-usd`, `eval --suite answers --cheap --yes`, `spend --by-purpose`, `paid unlock|lock`.
- **Models** are pinned in `config/pinned.toml` (Haiku 5.5, Opus 5.5 for onboarding, Qwen3 30B-A3B as the cheap answerer). The local-model and bake-off code was removed in Q6.

## Setup and keys
Misty ships with no keys, accounts or personal data. Everything secret lives in your own macOS Keychain, never in this repo.
- **OpenRouter** (all model calls): get a key at openrouter.ai, then `cd engine && uv run misty keys set openrouter`.
- **Google** (optional, read-only Gmail and Calendar plus calendar adds and Gmail drafts): create your own OAuth client in Google Cloud, set `MISTY_GOOGLE_ACCOUNT=you@yourdomain.com`, and import the client JSON in the app's setup.
- **Signing** (Mac app): put your codesign identity in `config/signing.local` and your team in `app/Signing.local.xcconfig` (both gitignored). Change `bundleIdPrefix` in `app/project.yml` to your own.
- Zero data retention is on by default (`allow_no_zdr: false`). Local, git-ignored files: `tests/golden*.yaml` (copy the `*.example.yaml` versions).

Some defaults still assume the author's setup, for example Singapore time for schedules and the IB-style tutoring prompts. Edit `engine/src/misty_engine/config.py` and `clock.py` for yours.

## License
MIT, see `LICENSE`. Third-party code and fonts keep their own licenses (`scripts/fluid_mini/LICENSE`, `app/Misty/Resources/Fonts/OFL-BricolageGrotesque.txt`).
