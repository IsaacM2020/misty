# Strong and cheap-answer models on OpenRouter, ZDR routes (Q0 Task 0.2, checked 2026-10-15)

Sources: `https://openrouter.ai/api/v1/models` (ids, list prices) and `https://openrouter.ai/api/v1/endpoints/zdr`
(every endpoint that is ZDR, with provider tag, price, context and supported parameters). Both fetched 2026-10-15.
Rule: a model with no ZDR endpoint is not a candidate. Every request still sets `provider.zdr = true`.

## strong: `anthropic/claude-opus-5.5`
- Used for: life areas at onboarding (one call, about 15k tokens in), and top-importance summaries if Q5 says so.
- ZDR endpoints: `google-vertex/global` and `amazon-bedrock` at $4.00 in / $20.00 out / $0.20 cache read per 1M
  (regional endpoints +10%). Context 1,000,000, max output 128,000. Pinned order: `google-vertex/global`, `amazon-bedrock`.
- Not on ZDR at all: `anthropic/claude-fable-5.1` and `claude-fable-5` (no ZDR endpoint in the list), so not candidates.
  Next best on ZDR: `claude-opus-5` ($5/$25), `claude-sonnet-5.5` ($2/$10, global vertex and bedrock).
- Live check (tiny call, `{"ok": true}` reply): served by Google (Vertex), HTTP 200, cost $0.000272.
- **Reasoning is mandatory on this route.** `reasoning: {"enabled": false}` returns HTTP 400
  "Reasoning is mandatory for this endpoint and cannot be disabled." `reasoning: {"effort": "low"}` is accepted.
  Q1's ApiRunner must therefore not send `enabled: false` for model key `strong`, and must leave headroom in
  `max_tokens` for reasoning tokens (the P27 lesson, again).

## answer_cheap: `google/gemma-3-27b-it`
- Used for: the eval's model-swap test (`answer_cheap` vs the production answerer), nothing else.
- Why this one: cheaper than Haiku 5.5 ($0.10 / $0.50) on both sides, a non-Anthropic family, no hidden reasoning
  tokens (predictable cost), 131k context, `response_format` and `structured_outputs` supported, solid at answering
  from a given text. It is not the literal cheapest ZDR model: that is `mistralai/mistral-nemo` at $0.018 / $0.030,
  a much weaker floor. Alternates the eval can try with a one-line change: `openai/gpt-oss-120b` (CoreWeave
  $0.03 / $0.17, reasoning model), `deepseek/deepseek-v4.1-flash` (Decart/DeepInfra $0.09-0.14 / $0.18-0.42, 1M context).
- ZDR endpoints: `deepinfra/fp8` $0.08 in / $0.16 out (16k max output), `nebius/fp8` $0.10 / $0.30,
  `nextbit/fp8` $0.08 / $0.35, `parasail/fp8` $0.08 / $0.45. Pinned order: `deepinfra/fp8`, `nebius/fp8`.
- Live check (tiny call with `response_format: json_object`): served by DeepInfra, HTTP 200, 18 in / 6 out tokens,
  cost $0.0000024, reply `{"ok": true}`.

## Spend for this check
Five tiny calls, total about $0.0008 (three on Opus 5.5 at $0.000272, one on Gemma 3 at $0.0000024, one rejected 400
that cost nothing). Calls went from a throwaway script with a temporary DB, not through `~/Misty`.
