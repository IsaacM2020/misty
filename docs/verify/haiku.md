# Claude Haiku 5.5 on OpenRouter, ZDR route (M0 Task 0.3, checked 2026-10-14)

Verified against `https://openrouter.ai/api/v1/models/anthropic/claude-haiku-5.5/endpoints`,
`https://openrouter.ai/api/v1/endpoints/zdr` and the prompt-caching / usage docs. No paid call made.

- Model id: `anthropic/claude-haiku-5.5` (alias `~anthropic/claude-haiku-latest`; batch variant `:batch`).
- Endpoints (provider.order slugs) and ZDR status:
  - ZDR yes: `google-vertex/global`, `google-vertex/us`, `google-vertex/europe`, `amazon-bedrock`, `amazon-bedrock/us-east-1`, `amazon-bedrock/eu-west-1`.
  - ZDR no: `anthropic`, `claude-on-aws`, `azure/global`, `azure/us`.
- Prices per 1M tokens on `google-vertex/global` and base `amazon-bedrock`: input $0.10, output $0.50, cache read $0.01,
  cache write (5 min) $0.125. Regional endpoints (`us`, `europe`, `us-east-1`, `eu-west-1`) cost +10%.
  Prompts over 100K tokens cost 5x (we never send those).
- Routing we use: `provider: {"zdr": true, "data_collection": "deny", "order": ["google-vertex/global", "amazon-bedrock"], "allow_fallbacks": true}`.
  Per-request `zdr` is OR-ed with account settings: it can switch ZDR on, never off.
- Prompt caching: `cache_control: {"type": "ephemeral"}` on a content part of the system message (up to 4 breakpoints,
  optional `"ttl": "1h"`). **Minimum cacheable prompt: 512 tokens for Claude Haiku 5.5** (doc: prompt-caching.md, "Minimum token requirements").
  Caching with `provider.order` set turns off sticky routing, which is fine for us.
- Usage/cost: always returned in `usage` (`cost` in USD, `prompt_tokens_details.cached_tokens`, `cache_write_tokens`).
  `usage: {include: true}` is deprecated and does nothing, so we don't send it.
- Errors: 404 when no endpoint matches the routing rules; 503 "no available model provider that meets your routing requirements".
  Key credit limit: docs conflict between 402 and 403, so classify by `error.metadata.limit_source` (e.g. `openrouter_key_limit`).
- Dashboard: Settings > Privacy (https://openrouter.ai/settings/privacy) has a ZDR toggle per model group (Anthropic, OpenAI,
  Google, ...). Exact toggle wording needs a login, so Isaac gets "find the ZDR option" steps and I confirm on screen.
  Keys page > Create API Key > "Key limit" > Credit limit.
