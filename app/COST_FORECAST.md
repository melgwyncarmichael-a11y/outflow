# Cost forecast — Invoice & Receipt Collector

OpenRouter API spend for the two models in `../Project 6203.md`.
Token counts are estimates — receipt-image tokenisation is the main unknown and
could be 2–3× off. Prices are recent OpenRouter rates; **confirm live before the
demo** (config cell → `VISION_MODEL`, `CHAT_MODEL`).

## Rate assumptions

| Model | Role | Input | Output |
|---|---|---|---|
| Qwen2.5-VL-72B | Module 1 — extraction | ~$0.3–0.9 / M tokens | ~$0.3–0.9 / M tokens |
| DeepSeek Chat V3.2 | Module 2 — query & summary | ~$0.28 / M | ~$0.42 / M |

## Cost per task

| Task | ~Tokens (in / out) | ~Cost |
|---|---|---|
| Module 1 — one **receipt image** extraction | 1.3k / 40 | $0.0005 – $0.0015 |
| Module 1 — one **freeform text** extraction | 120 / 40 | < $0.0001 |
| Module 2 — query, **variant A** (bare model) | 20 / 150 | ~$0.0001 |
| Module 2 — query, **variant B** (full 68-row dump) | 1.2k / 200 | ~$0.0004 |
| Module 2 — query, **variant C** (retrieval + 2–4 tool round-trips) | 1.8k / 300 | ~$0.001 |
| Summary tab (1 narrative; charts are local/free) | 1.5k / 400 | ~$0.001 |

## Cost per bigger unit

| Unit | ~Cost |
|---|---|
| One full **20 × 3 evaluation pass** (30 vision calls + 30 Module 2 runs) | **$0.05 – $0.15** |
| Stage 6 + Stage 7 combined (~10 eval passes + iteration + ad-hoc dev calls) | **$1 – $3** |
| Whole project, all teammates, generous estimate | **under $5** |

## What drives the cost

- **Vision calls dominate** — the receipt images, not DeepSeek. The Module 2
  query side is rounding error at this data size.
- **Variant B scales with ledger size** — trivial at 68 rows (~1k tokens); still
  only ~$0.004 per call at 1,000 rows.
- **Biggest cost risk is a model swap.** Moving Module 1 to a Gemini 2.x or
  GPT-4o-class vision model multiplies the vision line by ~5–15× — still a few
  dollars total, not tens.
- OpenRouter takes ~5.5% on credit top-ups; negligible at this scale.

## Bottom line

A full evaluation run costs pennies. Budget **$5 of OpenRouter credit** for the
entire assignment across the whole team and expect to use a fraction of it.
