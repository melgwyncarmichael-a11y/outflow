"""Smoke tests for outflow_core.py.

Plain assert-based, no pytest dependency, no network calls, no real
OpenRouter key needed — the client is faked. Run with:

    python3 test_outflow_core.py

This is a first pass, not the full evaluation harness (that's the
20-case suite in the notebook) — it exists to prove the notebook -> module
extraction didn't change behaviour, and to catch regressions like the
"maybe" / "may" bug the audit found before they reach a live demo.
"""

import base64
import datetime as dt
import json
import tempfile
from pathlib import Path

import pandas as pd

import outflow_core as core

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"FAILED: {label}")


# ============================================================================
# Pure functions
# ============================================================================

TODAY = dt.date(2026, 9, 9)

# The actual bug the audit found: "maybe" must NOT resolve to May.
start, end, note = core.resolve_relative_range("Did I maybe overspend this week?", TODAY)
check("'maybe' does not misfire as May", "may" not in note.lower() or "week" in note.lower())
check("'maybe' resolves to 'this week' instead", "this week" in note.lower())

start, end, note = core.resolve_relative_range("How much did I spend in May?", TODAY)
check("real 'May' still resolves correctly", start == "2026-05-01" and end == "2026-05-31")

start, end, note = core.resolve_relative_range("this month?", TODAY)
check("'this month' resolves to Sept 1-9", start == "2026-09-01" and end == "2026-09-09")

start, end, note = core.resolve_relative_range("last month?", TODAY)
check("'last month' resolves to August", start == "2026-08-01" and end == "2026-08-31")

start, end, note = core.resolve_relative_range("no date phrase here", TODAY)
check("no date phrase -> (None, None)", start is None and end is None)

status, unclear = core.extraction_status(
    {"date": None, "merchant": None, "amount": None, "description": None})
check("all-null -> unable", status == "unable" and len(unclear) == 4)

status, unclear = core.extraction_status(
    {"date": "2026-08-01", "merchant": "Cafe", "amount": None, "description": "lunch"})
check("one null -> partial", status == "partial" and unclear == ["amount"])

status, unclear = core.extraction_status(
    {"date": "2026-08-01", "merchant": "Cafe", "amount": 5.0, "description": "lunch"})
check("no nulls -> ok", status == "ok" and unclear == [])

# ---- image helpers (non-PDF passthrough path, no PyMuPDF needed) ----------
with tempfile.TemporaryDirectory() as tmp:
    fake_jpg = Path(tmp) / "receipt.jpg"
    raw = b"\xff\xd8\xff\xe0not a real jpeg but exercises the passthrough path"
    fake_jpg.write_bytes(raw)

    check("preview_bytes passthrough for non-PDF", core.preview_bytes(fake_jpg) == raw)
    check("render_page_bytes passthrough for non-PDF", core.render_page_bytes(fake_jpg) == raw)

    url = core.to_image_data_url(fake_jpg)
    check("to_image_data_url has jpeg mime", url.startswith("data:image/jpeg;base64,"))
    b64 = url.split(",", 1)[1]
    check("to_image_data_url round-trips", base64.b64decode(b64) == raw)


# ============================================================================
# Fake OpenRouter client — no network, no API key.
# ============================================================================

class FakeToolCall:
    def __init__(self, id_, name, arguments):
        self.id = id_
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none=True):
        d = {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls or None}
        return {k: v for k, v in d.items() if not (exclude_none and v is None)}


class FakeResponse:
    def __init__(self, content=None, tool_calls=None, usage=None):
        self.choices = [type("C", (), {"message": FakeMessage(content, tool_calls)})()]
        self.usage = usage or {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0005}


class FakeCompletions:
    def __init__(self, responses):
        self._queue = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._queue.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.chat = type("Chat", (), {"completions": FakeCompletions(responses)})()


def make_ledger():
    return pd.DataFrame([
        {"date": "2026-08-05", "merchant": "Trader Joe's", "amount": 20.0,
         "description": "groceries", "category": "Grocery"},
        {"date": "2026-08-15", "merchant": "Trader Joe's", "amount": 10.0,
         "description": "groceries", "category": "Grocery"},
        {"date": "2026-07-20", "merchant": "Campus Cafe", "amount": 5.0,
         "description": "coffee", "category": "Coffee"},
    ])


# ---- categorization / ledger ops (no client needed) -----------------------
with tempfile.TemporaryDirectory() as tmp:
    lookup_path = Path(tmp) / "category_lookup.xlsx"
    ledger = make_ledger()
    client = FakeClient([])  # unused in this block
    engine = core.OutflowEngine(client, "vision-model", "chat-model", lookup_path, ledger, TODAY)

    check("lookup seeded from ledger", set(engine.lookup_df["merchant"]) == {"Trader Joe's", "Campus Cafe"})
    check("categorize matches seeded merchant", engine.categorize("Trader Joe's") == "Grocery")
    check("categorize falls back to Uncategorized", engine.categorize("Some New Shop") == "Uncategorized")

    engine.add_category_mapping("Some New Shop", "Entertainment")
    check("add_category_mapping updates lookup_df", engine.categorize("Some New Shop") == "Entertainment")
    check("add_category_mapping persists to xlsx", lookup_path.exists())

    dupe = engine.check_duplicate("2026-08-05", "Trader Joe's", 20.0)
    check("check_duplicate finds an exact match", len(dupe) == 1)
    dupe2 = engine.check_duplicate("2026-08-05", "Trader Joe's", 99.0)
    check("check_duplicate finds no match on different amount", len(dupe2) == 0)

    spend = engine.calculate_spend("2026-08-01", "2026-08-31", category="Grocery")
    check("calculate_spend sums August Grocery correctly", spend == {"total": 30.0, "transaction_count": 2, "avg": 15.0})

    engine.append_row({"date": "2026-09-01", "merchant": "New Row", "amount": 1.0,
                        "description": "x", "category": "Uncategorized"})
    check("append_row grows the ledger", len(engine.ledger) == 4)
    check("data_max_date reflects the new row", engine.data_max_date == "2026-09-01")

# ---- Module 1a / Module 1 with a fake client --------------------------------
with tempfile.TemporaryDirectory() as tmp:
    lookup_path = Path(tmp) / "category_lookup.xlsx"
    ledger = make_ledger()

    triage_json = json.dumps({"receipt_type": "printed", "confident": True, "reason": "clean receipt"})
    client = FakeClient([FakeResponse(content=f"```json\n{triage_json}\n```")])
    engine = core.OutflowEngine(client, "vision-model", "chat-model", lookup_path, ledger, TODAY)

    fake_jpg = Path(tmp) / "r.jpg"
    fake_jpg.write_bytes(b"\xff\xd8\xff\xe0fake")
    result = engine.triage_receipt(fake_jpg)
    check("triage_receipt strips fences and parses JSON", result["receipt_type"] == "printed")

    extraction_json = json.dumps({"date": "2026-08-05", "merchant": "Cafe",
                                   "amount": 5.5, "description": "lunch"})
    client2 = FakeClient([FakeResponse(content=extraction_json)])
    engine2 = core.OutflowEngine(client2, "vision-model", "chat-model", lookup_path, ledger, TODAY)
    extracted = engine2.extract_expense("$5.50 lunch")
    check("extract_expense parses plain JSON (no fence)", extracted["amount"] == 5.5)

    client3 = FakeClient([FakeResponse(content="not valid json at all")])
    engine3 = core.OutflowEngine(client3, "vision-model", "chat-model", lookup_path, ledger, TODAY)
    extracted3 = engine3.extract_expense("garbled")
    check("extract_expense falls back to all-null on bad JSON", extracted3["amount"] is None)

# ---- Module 2 variants with a fake client -----------------------------------
with tempfile.TemporaryDirectory() as tmp:
    lookup_path = Path(tmp) / "category_lookup.xlsx"
    ledger = make_ledger()

    client_a = FakeClient([FakeResponse(content="I don't have access to your data.")])
    engine_a = core.OutflowEngine(client_a, "vision-model", "chat-model", lookup_path, ledger, TODAY)
    ans_a = engine_a.answer_query("How much did I spend?", variant="A")
    check("variant A calls the model with no system prompt", "data" in ans_a.lower())
    check("variant A sends only the bare question", client_a.chat.completions.calls[0]["messages"] == [
        {"role": "user", "content": "How much did I spend?"}])

    client_b = FakeClient([FakeResponse(content="You spent $35.00 total.")])
    engine_b = core.OutflowEngine(client_b, "vision-model", "chat-model", lookup_path, ledger, TODAY)
    ans_b = engine_b.answer_query("How much did I spend total?", variant="B")
    check("variant B dumps the full ledger into context", "Trader Joe's" in
          client_b.chat.completions.calls[0]["messages"][1]["content"])

    # variant C: one tool-call round, then a final answer
    tool_call = FakeToolCall("call_1", "calculate_spend",
                              json.dumps({"start_date": "2026-08-01", "end_date": "2026-08-31"}))
    resp1 = FakeResponse(tool_calls=[tool_call])
    resp2 = FakeResponse(content="You spent $30.00 on groceries in August across 2 transactions.")
    client_c = FakeClient([resp1, resp2])
    engine_c = core.OutflowEngine(client_c, "vision-model", "chat-model", lookup_path, ledger, TODAY)
    ans_c = engine_c.answer_query("How much did I spend in August?", variant="C")
    check("variant C completes the tool-call round trip", "30.00" in ans_c)

    # variant C: loop exhausts (model keeps calling tools) -> explicit message, not blank
    endless_tool_call = FakeToolCall("call_x", "calculate_spend",
                                      json.dumps({"start_date": "2026-08-01", "end_date": "2026-08-31"}))
    client_exhaust = FakeClient([FakeResponse(tool_calls=[endless_tool_call]) for _ in range(4)])
    engine_exhaust = core.OutflowEngine(client_exhaust, "vision-model", "chat-model",
                                         lookup_path, ledger, TODAY)
    ans_exhaust = engine_exhaust.answer_query("compound question", variant="C")
    check("exhausted tool loop returns an explicit message, not blank",
          ans_exhaust != "" and "wasn't able" in ans_exhaust)

# ---- CostMeter ---------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    lookup_path = Path(tmp) / "category_lookup.xlsx"
    ledger = make_ledger()
    usage = {"prompt_tokens": 50, "completion_tokens": 10, "cost": 0.0002}
    client = FakeClient([FakeResponse(content="hi", usage=usage),
                         FakeResponse(content="hi again", usage=usage)])
    meter = core.CostMeter()
    meter.verbose = False
    meter.wrap(client)
    client.chat.completions.create(model="deepseek/deepseek-v3.2", messages=[])
    check("CostMeter records a billed call", len(meter.calls) == 1 and meter.calls[0]["source"] == "billed")
    check("CostMeter total matches the billed cost", abs(meter.total - 0.0002) < 1e-9)

    already_wrapped = getattr(client.chat.completions.create, "_metered", False)
    meter.wrap(client)  # re-wrap should be a no-op
    client.chat.completions.create(model="deepseek/deepseek-v3.2", messages=[])
    check("wrap() is idempotent (no double-counting on re-wrap)", len(meter.calls) == 2)


# ============================================================================
print()
if failed:
    print(f"{passed} passed, {len(failed)} FAILED:")
    for f in failed:
        print(f"  - {f}")
    raise SystemExit(1)
else:
    print(f"All {passed} checks passed.")
