# Background job cost: what to run when OpenRouter credit is back

Per-job model lives in settings `job_models` ({"compile","summarise","judge"} -> "haiku" or "cheap").
"cheap" = `answer_cheap` (qwen3-30b-a3b, $0.10 in / $0.30 out). Flip a job only if its eval is within ~3% of Haiku.
Live compile now waits for `live_compile_min_items` (6) or `live_compile_max_wait_min` (30) before a call.

Run each pair on a small sample (paid, needs credit). Compare the two reports by hand:

    cd engine && uv run misty-engine build --sample 40 --dry-run          # estimate only, free
    cd engine && uv run misty-engine eval --suite answers --yes           # Haiku baseline
    cd engine && uv run misty-engine eval --suite answers --yes --cheap   # cheap model

Judge (name matching): `golden.dups.example.yaml` in tests/ is the golden pair set; run the existing dup golden test
with settings job_models.judge = "cheap" and compare same/different/unsure agreement against Haiku. Switch only at >= 97% agreement
and zero "same" verdicts that Haiku called "different".
Summarise: re-summarise 20 entities with each model, read 20 side by side (facts invented? people/project fields right?).
Compile: 40 items each, compare `do` lines (the card text) and people lists; that is where cheap models slip.
