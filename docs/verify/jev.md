# Jev 1.13 System One on OpenRouter (M0 Task 0.2, checked 2026-10-14)

Verified against OpenRouter's public OpenAPI file (https://openrouter.ai/openapi.yaml, path `/systemone`, operationId
`createSystemone`) and the public endpoints API. No paid call made.

- Model id: `typesafe/jev-1.13` (bare `jev-1.13` is also mapped into the `typesafe/` namespace). Alias `~typesafe/jev-latest`.
- Price: $0.042 / M input tokens, $0 output. Only provider: TypeSafe (`typesafe/jev-1.13-20260917`).
- ZDR: TypeSafe's endpoint IS on https://openrouter.ai/api/v1/endpoints/zdr. `provider.zdr` is in the request schema (ProviderPreferences).
- Limits: context 64,000, max prompt 32,000 tokens (our excerpt cap is 1,500 + card, far below).
- Endpoint: `POST https://openrouter.ai/api/v1/systemone`.

## Request
`{model, state, questions, provider?, session_id?, trace?, user?}`
- `state`: string, object or array (the content to judge).
- `questions`: object keyed by our own question names. Each has `type` and `instructions`:
  - `noul` (yes/no): optional `criteria: {"true": "...", "false": "..."}`.
  - `choice`: `criteria` is an object `{option_key: description}`.
  - `score`: `criteria` is an ordered array.
- **No native multi-select.** Docs workaround: one `noul` per option. So `sources` becomes 7 `noul` questions
  (`src_files`, `src_mail`, ...) and Misty treats `noul >= 0.5` as selected. Same information as spec 8.2, different wire shape.

## Response
`{id, model, provider, answers: {key: ...}, usage: {input_tokens, output_tokens, cost}}`
- `noul`: `{type, noul}` where `noul` is P(true). Confidence for the 0.6 rule = `max(p, 1-p)`.
- `choice`: `{type, choice, confidence, probabilities}`.
- `score`: `{type, score, confidence, probabilities, legend}`.
- `usage.cost` is USD for the call.
- Errors: 400, 401, 402, 403, 404, 413, 429, 500, 502, 503, 524, 529, body `{"error": {"code","message","metadata"}}`.

Fixtures (verbatim from the OpenAPI examples): `tests/engine/fixtures/jev_request_example.json`, `jev_response_example.json`.

## Still to confirm at the first paid call (M3 Task 3.7)
- That `"provider": {"zdr": true}` is accepted on `/systemone` in practice (schema says yes).
- That `model: "typesafe/jev-1.13"` is accepted (OpenAPI example uses it; the SDK page uses bare `jev-1.13`).
