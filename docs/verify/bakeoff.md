# M5 local model bake-off (2026-10-15)

20 compiled items from the first build (stratified by source), each model writes the note locally,
`claude -p` (Sonnet) grades it against the Haiku 5.5 note already in the wiki. No note text kept here.
Limits: json_ok >= 90%, peak RAM <= 9 GB, >= 15 tok/s.

| model | json_ok | tok/s | peak GB | score/15 | invented names |
|---|---|---|---|---|---|
| mlx-community/gemma-4-e4b-it-4bit | 95% | 18.7 | 6.2 | 10.8 | 1 |
| mlx-community/Qwen3.5-9B-MLX-4bit | 100% | 8.8 | 7.8 | 11.1 | 2 (too slow) |
| mlx-community/gemma-4-12B-it-4bit | 95% | 5.3 | 8.7 | 11.1 | 1 (too slow) |

Winner: gemma-4-e4b-it-4bit (kept in the HF cache, 5.2 GB). Losers deleted.
NOT pinned yet: Isaac leans to Haiku 5.5 (ZDR) for live compile because of RAM/GPU load; decision pending.
To pin: `[local_model] model_id = "mlx-community/gemma-4-e4b-it-4bit"` in config/pinned.toml.
