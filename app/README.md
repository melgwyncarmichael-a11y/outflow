# Invoice & Receipt Collector — prototype

Colab notebook that implements the design in `../Project 6203.md`.
**The notebook is the app** — there is no separate server.

## Files

| File | What it is |
|---|---|
| `Expense_Receipt_Collector.ipynb` | The prototype. Open in Google Colab, Run all. |
| `../Expense Data (July to August).csv` | Seed ledger (68 rows). Loaded on start. |
| `../Receipt Samples/` | Synthetic receipts used by Module 1 and the eval cases. |
| `category_lookup.xlsx` | Merchant → category master table. Auto-created on first run from the seed data; maintain it by hand in Drive after that. |
| `eval_outputs_for_scoring.csv` | Written by the eval cell. 60 rows (20 cases × 3 variants) for blind human scoring. |

## What each teammate does once

1. **OpenRouter key** — make one at <https://openrouter.ai/keys>.
2. **Add it as a Colab secret** — open the notebook → 🔑 Secrets panel →
   new secret named `OPENROUTER_API_KEY`, value = your key, *Notebook access* ON.
   Secrets are per Google account; they are not stored in the notebook, so
   everyone does this on their own.
3. **Shortcut the shared folder into My Drive** — right-click the shared
   `group assignment` folder in Drive → Organise → Add shortcut to Drive → My Drive.

## Every session

- `Runtime → Run all`. CPU runtime is fine, no GPU.
- Allow *Mount Google Drive* when asked.
- If the `PROJECT_DIR` line in the config cell doesn't match where the shared
  folder sits for you, edit that one line.

## Before the demo (Stage 5 / Stage 6)

- Confirm `VISION_MODEL` and `CHAT_MODEL` in the config cell are live routes on
  OpenRouter, and that the DeepSeek route supports tool calling.
- Keep the shared Drive as the source of truth. If someone edits the notebook,
  use *File → Save a copy in Drive* into the shared folder, or edit it in place —
  don't fork it into personal Drives.

## Deliverable link

Append the notebook's **Share** link (Anyone with the link – Viewer, inside the
shared Drive) to the end of the report.
