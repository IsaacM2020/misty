# `claude -p` for stage 5 (M0 Task 0.7, checked 2026-10-14, Claude Code CLI on this Mac)

Docs: https://code.claude.com/docs/en/cli-reference, /headless, /authentication, /errors, /env-vars.

- Flags that skip user config AND keep the subscription login: `--safe-mode` (no CLAUDE.md, skills, plugins, hooks, MCP,
  commands, agents, auto memory; auth and model selection work normally).
- `--bare` does NOT work for us: it ignores the subscription login and wants an API key.
- `--tools ""` disables every built-in tool. `--model sonnet` is a valid alias. `--output-format json` returns
  `{is_error, result, total_cost_usd, usage, modelUsage, duration_ms, num_turns, stop_reason, ...}`.
- Auth precedence: if `ANTHROPIC_API_KEY` is set, `-p` ALWAYS uses it (API billing). So the runner env must not contain it.
- Usage-limit text (doc quote, fixture `tests/engine/fixtures/claude_quota_example.txt`):
  `You've hit your session limit · resets 3:45pm` / `... weekly limit · resets Mon 12:00am`. No special exit code.
  Parse `resets <time>` in local time, fall back to now + 1 h.

## Probe on this Mac (2 real calls, subscription)
Command: `env -i PATH HOME USER LOGNAME LANG claude -p --safe-mode --tools "" --model sonnet --output-format json "<prompt>"`
- With only `PATH HOME LANG` the CLI says `Not logged in` (keychain lookup needs `USER`/`LOGNAME`). With `USER` and `LOGNAME`
  added it works. **Runner env = PATH, HOME, USER, LOGNAME, LANG** (plan said PATH, HOME, LANG: corrected).
- Probe 1: asked whether it had any info on a person named Isaac / projects / memory. Answer contained none of Isaac's data
  (it described only the built-in agent guidelines). `/tmp/isaacOS_session.txt` and `projects/session-log.md` mtimes unchanged,
  so no Stop hooks fired.
- Cost of the probe: about $0.011 notional on the Pro plan.
- Not re-probed (kept quota): tool-disable effect beyond docs. M4 Task 4.1 re-checks it inside the real runner test (`-m live`).
- Note: the CLI's logged-in account on this Mac is the Pro plan, registered to an address that is NOT Isaac's Google account.
  Misty never uses that address for Google data (rule 6); `claude -p` only borrows the subscription quota, same as Claude Code itself.
