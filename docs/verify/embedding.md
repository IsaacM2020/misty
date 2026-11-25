# EmbeddingGemma 2 on the M4 (M0 Task 0.4, checked 2026-10-14)

Sources: https://huggingface.co/google/embeddinggemma-2 (model card), https://huggingface.co/mlx-community/embeddinggemma-2-bf16 (MLX conversion).

## Facts
- Official repo `google/embeddinggemma-2`: Apache-2.0, **not gated** (no licence step, no HF login needed).
- 740M params total = 270M text + 170M vision + 300M audio. Output 768-d (Matryoshka 512/256/128 possible). Context 8,192 tokens.
- Text AND images land in one shared vector space (so text queries can find photos). Image = 280 tokens each by default.
- Text prefixes (images get none): query `task: search result | query: {q}`, document `title: {title} | text: {content}`
  (`title: none` when there's no title).
- Text+image only mode loads 440M (`config_kwargs={"audio_config": None}` in sentence-transformers).
- MLX: `mlx-community/embeddinggemma-2-bf16` (1.49 GB, BF16, cosine vs FP32 >= 0.9999). Needs `mlx>=0.32.3`,
  `transformers>=5.18.0`, and `mlx-vlm` from git commit `3d87e88402f307efbf68e568971aa887ee7d9ed0`
  (the README says the PyPI build does not have EmbeddingGemma 2 yet). Pinned snapshot `1a4ffddb7905d3f63486748deabe091a01fb6201`.
- **Image preprocessing needs `torch` + `torchvision` installed** (the processor imports them). The model compute stays on MLX.

## Benchmark on this Mac (scripts/m0/bench_embed.py, MLX, 16 GB M4)
| check | result |
|---|---|
| semantic sanity (cricket query vs 3 docs) | correct doc ranked first |
| output | 768-d, unit-normalised |
| text throughput | 28.6 docs/s (256 paragraphs, ~274 tokens each, batch 16) |
| image throughput | 1.7 images/s (one at a time, 448x448) |
| text -> image retrieval | "red circle" and "blue square" both matched the right image |
| peak RSS | 1.6 GB with the text model alone; 2.7 GB in the bench because it loaded the model twice and imported torch |
| peak GPU (MLX) | 3.0 GB text, 3.7 GB with images |

## Decision
Backend = **MLX**. I did not run a PyTorch-MPS comparison: MLX already clears the bar (>= 5 docs/s, text RSS <= 2 GB, images
work), PyTorch would add a 3 GB+ model copy on a disk with 33 GB free, and the plan's rule only needs a winner under the limits.
Isaac can ask for the MPS run if he wants the comparison on paper.

## Notes carried into M2 / M6
- Load the model ONCE in the engine (the bench loaded it twice). Import torch lazily, only when an image batch is processed.
- Images: 1.7 img/s unbatched means ~50 min per 5,000 photos. M6 Task 6.10 batches images and prints an estimate first.
