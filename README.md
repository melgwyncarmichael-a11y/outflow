# Outflow

> An expense tracker you talk to instead of maintaining.

Outflow is a GenAI expense tracker built for grad students on a fixed budget.
Log a receipt photo or a one-line note, confirm what the AI read off it, and
ask plain-English questions about your spending instead of digging through a
spreadsheet.

Built for the **PE6203 Generative AI and Agentic AI** group assignment at
Nanyang Technological University.

## How it works

1. **Add** — upload a synthetic receipt image or type a note ("$4 on chicken rice today").
2. **Vision extraction** (Qwen2.5-VL-72B via OpenRouter) reads it into structured JSON — nulls, never guesses, when a field can't be read.
3. **Receipt triage** (same vision model, a separate call) flags anything that isn't a legitimate receipt before it ever reaches extraction.
4. **Human review** — an editable row next to the receipt image. Nothing saves without confirmation; a flagged receipt needs an explicit override.
5. **Rule-based categorization** — merchant looked up against an Excel master table, no AI involved. No match → `Uncategorized`, sorted by a human.
6. **Ask** — natural-language questions and a monthly summary (DeepSeek V3.2 via OpenRouter), grounded in rule-based retrieval over your own data, with all arithmetic delegated to a calculator tool. Charts are drawn by pandas/matplotlib, never by the LLM.

See [`docs/Flow diagram v2.pdf`](docs/Flow%20diagram%20v2.pdf) for the full architecture, and [`app/Project 6203.md`](app/Project%206203.md) for the complete design spec.

## Repo layout

| Folder | Contents |
|---|---|
| [`app/`](app) | The Colab notebooks (V1 baseline + V2), design spec, cost forecast, and setup instructions |
| [`data/`](data) | Seed transaction ledger, category lookup table, and synthetic receipt samples |
| [`evaluation/`](evaluation) | V1→V2 failure analysis, the eval matrix, and raw model outputs from the 20-case test suite |
| [`docs/`](docs) | Assignment brief, team design brief, and architecture diagrams |
| [`presentation/`](presentation) | Slide deck, demo video, and demo script |

## Running it

Outflow runs as a Colab notebook — the notebook *is* the app. Open
[`app/Expense_Receipt_Collector_v2.ipynb`](app/Expense_Receipt_Collector_v2.ipynb)
in Google Colab, add your own OpenRouter API key as a Colab secret, and run
the cells top to bottom. Full setup steps are in [`app/README.md`](app/README.md).

## Stack

Qwen2.5-VL-72B-Instruct + DeepSeek V3.2, both via [OpenRouter](https://openrouter.ai) · pandas / matplotlib · Google Colab + ipywidgets

## Evaluation

Compared three variants — (A) a bare LLM, (B) prompt + full data dump with no tools, (C) the full system with rule-based retrieval and tool-calling — across 20 test cases (10 extraction, 10 query/summary), scored by hand on grounding, correctness, readability, and safety. Full methodology and results are in [`evaluation/`](evaluation).

---

*Design, prompts, evaluation criteria, and every architecture decision are the team's own. AI tools (Claude, Claude Design) were used to scaffold the notebook UI, wire up API calls, and draft supporting documents — not to make the design decisions. See [`app/Project 6203.md`](app/Project%206203.md) for the full attribution.*
