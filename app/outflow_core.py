"""Outflow core logic — AI modules, prompts, and rule-based rules.

Extracted out of the notebook (Expense_Receipt_Collector_v2.ipynb) so this
logic is plain, editable Python instead of notebook-JSON. The notebook
constructs an OpenRouter client, wraps it in an OutflowEngine, and calls
into this module for every AI-module and rule-based operation; this file
makes no network calls or file writes except through the client and paths
it's handed.

Prompts, model choices, the retrieval/tool-calling design, and every
architecture decision here are unchanged from the team's spec
(Project 6203.md) — this file relocates code, it does not change behaviour.
See V1_TO_V2_CHANGES.md for what changed on the way to this version, and
the audit notes in the repo history for why this file exists at all.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd


# ============================================================================
# Pure helpers — no client, no ledger. Safe to unit test directly.
# ============================================================================

def render_page_bytes(path: Path, dpi: int = 150) -> bytes:
    """Raw image bytes for a receipt. PDFs render page 1 to PNG at `dpi`;
    everything else is returned as-is. Shared by to_image_data_url (needs
    high DPI for OCR accuracy) and preview_bytes (just needs to look right
    and load fast) — previously two separate re-implementations."""
    data = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        return doc[0].get_pixmap(dpi=dpi).tobytes("png")
    return data


def to_image_data_url(path: Path) -> str:
    """Base64 data: URL for a model call (150 DPI)."""
    data = render_page_bytes(path, dpi=150)
    if path.suffix.lower() == ".pdf":
        mime = "image/png"
    elif path.suffix.lower() in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    else:
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def preview_bytes(path: Path) -> bytes:
    """Lower-DPI raster for the review-UI preview widget (120 DPI)."""
    return render_page_bytes(path, dpi=120)


def extraction_status(parsed: dict):
    """Derive ok/partial/unable from the null pattern in an extraction
    result. This is a plain code interpretation, not a model field — Module
    1's own output schema stays exactly the 4 fields the spec requires."""
    fields = ["date", "merchant", "amount", "description"]
    unclear = [f for f in fields if parsed.get(f) in (None, "", "null")]
    if len(unclear) == len(fields):
        return "unable", unclear
    return ("partial" if unclear else "ok"), unclear


def resolve_relative_range(question: str, today: dt.date):
    """Rule-based (token-matching) date phrase -> (start, end, note). No LLM.

    Uses word-boundary matching for month names — a plain substring check
    (`"may" in q`) used to misfire on any question containing "maybe"."""
    q = question.lower()

    def s(d):
        return d.isoformat()

    if "yesterday" in q:
        d = today - dt.timedelta(days=1)
        return s(d), s(d), f"Interpreting 'yesterday' as {s(d)}."
    if "today" in q:
        return s(today), s(today), f"Interpreting 'today' as {s(today)}."
    if "last week" in q:
        end = today - dt.timedelta(days=1)
        start = end - dt.timedelta(days=6)
        return s(start), s(end), f"Interpreting 'last week' as {s(start)} to {s(end)}."
    if "this week" in q:
        start = today - dt.timedelta(days=today.weekday())
        return s(start), s(today), f"Interpreting 'this week' as {s(start)} to {s(today)} (so far)."
    if "last month" in q:
        first_this = today.replace(day=1)
        last_end = first_this - dt.timedelta(days=1)
        last_start = last_end.replace(day=1)
        return (s(last_start), s(last_end),
                f"Interpreting 'last month' as {last_start:%B %Y}.")
    if "this month" in q:
        start = today.replace(day=1)
        return (s(start), s(today),
                f"Interpreting 'this month' as {start:%B %Y} so far (through {s(today)}).")

    months = ["january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december"]
    for i, name in enumerate(months, start=1):
        if re.search(rf"\b{name}\b", q):
            year = today.year
            start = dt.date(year, i, 1)
            end = (dt.date(year, i + 1, 1) - dt.timedelta(days=1)) if i < 12 \
                  else dt.date(year, 12, 31)
            return s(start), s(end), f"Interpreting '{name}' as {name.title()} {year}."

    return None, None, "No specific date range detected — using the full transaction history."


def rows_to_text(df: pd.DataFrame) -> str:
    return "(no transactions)" if df.empty else df.to_csv(index=False)


def _strip_json_fence(raw: str) -> str:
    return raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


# ============================================================================
# Prompts and schemas — verbatim from Section 3 of the report.
# ============================================================================

TRIAGE_SYSTEM = (
    "You classify the AUTHENTICITY of a receipt image, not its contents.\n"
    "Return ONLY JSON with three keys: receipt_type, confident, reason.\n"
    "receipt_type must be one of: printed, handwritten, hand_drawn, digital,\n"
    "not_a_receipt, unclear. confident is true or false. reason is a short string.\n"
    "hand_drawn means someone sketched a receipt-like layout by hand instead of\n"
    "a real or generated receipt. not_a_receipt means the image is not a receipt\n"
    "at all. Do not extract any expense data here - that happens separately."
)

NEEDS_REVIEW_TYPES = {"handwritten", "hand_drawn", "not_a_receipt", "unclear"}

MODULE1_SYSTEM = (
    "You are extracting structured data from a receipt image or a short expense\n"
    "note. Return ONLY valid JSON matching this schema:\n"
    '{"date": "YYYY-MM-DD", "merchant": string, "amount": number, "description": string}\n'
    "If a field cannot be determined, set it to null. Do not guess amounts.\n"
    "Extract the receipt's grand total and merchant - do NOT list or sum up\n"
    "individual line items yourself, and do not infer a category.\n"
    "Do not include any text outside the JSON object."
)

MODULE1_FEWSHOT = [
    {"role": "user", "content":
        "Expense note: Receipt shows lunch at a cafe but the total line is torn "
        "off and unreadable."},
    {"role": "assistant", "content":
        '{"date": null, "merchant": null, "amount": null, "description": null}'},
    {"role": "user", "content": "Expense note: spent some money on lunch"},
    {"role": "assistant", "content":
        '{"date": null, "merchant": null, "amount": null, "description": "lunch"}'},
]

MODULE2_SYSTEM = (
    "You answer questions about the user's personal spending using ONLY the\n"
    "transaction data provided to you. Never compute totals or averages\n"
    "yourself - always call the calculate_spend tool for any arithmetic.\n"
    "If the data needed to answer is not present, say so explicitly rather\n"
    "than guessing. Do not give general financial or investment advice.\n"
    "Time references like 'this month' or 'last week' are pre-resolved for you\n"
    "into an exact date range shown below - state that range in your answer.\n"
    "If the resolved range has no transactions, or only partial data, say so\n"
    "explicitly instead of returning a number with no explanation."
)

MODULE2_FEWSHOT = [
    {"role": "user", "content": (
        "Today is 2026-09-09. Data available from 2026-07-09 to 2026-08-28.\n"
        "Interpreting 'this month' as September 2026 so far (through 2026-09-09).\n"
        "Relevant transactions (2026-09-01 to 2026-09-09):\n(no transactions)\n\n"
        "Question: How much did I spend this month?"
    )},
    {"role": "assistant", "content": (
        "You haven't logged any transactions for September 2026 yet (the data "
        "runs through 28 August 2026), so I can't report a September total."
    )},
]

CALC_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_spend",
        "description": "Sum / count / average spend over a date range and optional category.",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "category": {"type": "string"},
            },
            "required": ["start_date", "end_date"],
        },
    },
}


# ============================================================================
# CostMeter — wraps a client's chat.completions.create, tracks $ per call.
# ============================================================================

class CostMeter:
    # Offline fallback prices, USD per 1M tokens. Only used when OpenRouter
    # does NOT return an actual cost. EDIT to match your live routes.
    PRICES = {
        "qwen/qwen2.5-vl-72b-instruct": {"in": 0.25, "out": 0.75},
        "deepseek/deepseek-v3.2": {"in": 0.21, "out": 0.31},
    }

    def __init__(self):
        self.calls = []
        self.verbose = True

    @staticmethod
    def _get(usage, key, default=0):
        if usage is None:
            return default
        if isinstance(usage, dict):
            return usage.get(key, default) or default
        v = getattr(usage, key, None)
        if v is None:
            v = (getattr(usage, "model_extra", None) or {}).get(key)
        return default if v is None else v

    def _price(self, model, pt, ct):
        p = self.PRICES.get(model, {"in": 0.0, "out": 0.0})
        return pt / 1e6 * p["in"] + ct / 1e6 * p["out"]

    def record(self, model, usage):
        pt = int(self._get(usage, "prompt_tokens", 0))
        ct = int(self._get(usage, "completion_tokens", 0))
        real = self._get(usage, "cost", None)
        cost = float(real) if real not in (None, 0) else self._price(model, pt, ct)
        src = "billed" if real not in (None, 0) else "estimated"
        self.calls.append({"model": model, "prompt_tokens": pt,
                            "completion_tokens": ct, "cost_usd": cost, "source": src})
        if self.verbose:
            print(f"  [cost] +${cost:.5f} ({src})   running ${self.total:.4f}")
        return cost

    @property
    def total(self):
        return sum(c["cost_usd"] for c in self.calls)

    def summary(self):
        if not self.calls:
            print("No API calls recorded yet.")
            return None
        df = pd.DataFrame(self.calls)
        by_model = (df.groupby("model")
                    .agg(calls=("cost_usd", "size"),
                         prompt_tokens=("prompt_tokens", "sum"),
                         completion_tokens=("completion_tokens", "sum"),
                         cost_usd=("cost_usd", "sum"))
                    .round(6))
        print(by_model.to_string())
        print(f"\nTOTAL  {len(self.calls)} calls | "
              f"{df.prompt_tokens.sum():,} in + {df.completion_tokens.sum():,} out | "
              f"${self.total:.4f}")
        est = df.loc[df.source == "estimated", "cost_usd"].sum()
        if est:
            print(f"({est:.4f} of that is estimated from the price table, not billed)")
        return by_model

    def reset(self):
        self.calls.clear()
        print("cost meter reset")

    def wrap(self, client):
        """Monkey-patch client.chat.completions.create to record every call
        and ask OpenRouter for the real billed cost. Idempotent — wrapping
        the same client twice (e.g. on a cell re-run) is a no-op."""
        if getattr(client.chat.completions.create, "_metered", False):
            return client
        orig_create = client.chat.completions.create

        def metered_create(*args, **kwargs):
            extra_body = dict(kwargs.pop("extra_body", {}) or {})
            extra_body.setdefault("usage", {"include": True})
            kwargs["extra_body"] = extra_body
            resp = orig_create(*args, **kwargs)
            model = kwargs.get("model") or (args[0] if args else "unknown")
            try:
                self.record(model, getattr(resp, "usage", None))
            except Exception as e:
                print(f"  [cost] capture failed: {e}")
            return resp

        metered_create._metered = True
        client.chat.completions.create = metered_create
        return client


# ============================================================================
# OutflowEngine — the stateful pieces: client, models, ledger, lookup table.
# ============================================================================

class OutflowEngine:
    """One Outflow session.

    Holding the client, model names, ledger, and category lookup table on
    one object — instead of module-level globals mutated via `global`
    inside nested notebook closures — is the actual fix for the "tight
    coupling via global state" finding from the architecture audit. The
    notebook constructs exactly one of these and calls its methods; nothing
    else needs `global` statements anymore.
    """

    def __init__(self, client, vision_model: str, chat_model: str,
                 lookup_xlsx_path: Path, ledger: pd.DataFrame,
                 today: Optional[dt.date] = None):
        self.client = client
        self.vision_model = vision_model
        self.chat_model = chat_model
        self.lookup_xlsx_path = Path(lookup_xlsx_path)
        self.ledger = ledger
        self.today = today or dt.date.today()
        self.lookup_df = self._load_lookup()

    # ---- data coverage, always live off the current ledger ---------------
    @property
    def data_min_date(self) -> str:
        return self.ledger["date"].min() if len(self.ledger) else self.today.isoformat()

    @property
    def data_max_date(self) -> str:
        return self.ledger["date"].max() if len(self.ledger) else self.today.isoformat()

    # ---- categorization (rule-based, no AI) -------------------------------
    def _load_lookup(self) -> pd.DataFrame:
        if self.lookup_xlsx_path.exists():
            lk = pd.read_excel(self.lookup_xlsx_path)
        else:
            seed = (self.ledger[["merchant", "category"]].drop_duplicates()
                    if len(self.ledger) else pd.DataFrame(columns=["merchant", "category"]))
            seed.to_excel(self.lookup_xlsx_path, index=False)
            lk = seed
        lk["merchant_l"] = lk["merchant"].str.lower().str.strip()
        return lk

    def categorize(self, merchant: str, description: str = "") -> str:
        m = (merchant or "").lower().strip()
        d = (description or "").lower().strip()
        for _, r in self.lookup_df.iterrows():
            key = r["merchant_l"]
            if key and (key in m or key in d):
                return r["category"]
        return "Uncategorized"

    def add_category_mapping(self, merchant: str, category: str):
        merchant = (merchant or "").strip()
        category = (category or "").strip()
        if not merchant or not category:
            return
        new_row = pd.DataFrame([{"merchant": merchant, "category": category,
                                  "merchant_l": merchant.lower()}])
        self.lookup_df = pd.concat([self.lookup_df, new_row], ignore_index=True)
        self.lookup_df[["merchant", "category"]].to_excel(self.lookup_xlsx_path, index=False)

    # ---- ledger ops (code, no AI) ------------------------------------------
    def check_duplicate(self, date: str, merchant: str, amount: float) -> pd.DataFrame:
        return self.ledger[
            (self.ledger["date"] == date) &
            (self.ledger["merchant"].str.lower() == (merchant or "").strip().lower()) &
            (self.ledger["amount"] == amount)
        ]

    def append_row(self, row: dict):
        self.ledger = pd.concat([self.ledger, pd.DataFrame([row])], ignore_index=True)

    def retrieve(self, start_date=None, end_date=None, category=None) -> pd.DataFrame:
        df = self.ledger.copy()
        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]
        if category:
            df = df[df["category"].str.lower() == str(category).lower()]
        return df

    def calculate_spend(self, start_date: str, end_date: str, category=None) -> dict:
        df = self.ledger.copy()
        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
        if category:
            df = df[df["category"].str.lower() == str(category).lower()]
        total = round(float(df["amount"].sum()), 2)
        n = int(len(df))
        return {"total": total, "transaction_count": n,
                "avg": round(total / n, 2) if n else 0.0}

    # ---- Module 1a - Receipt Triage ----------------------------------------
    def triage_receipt(self, path: Path) -> dict:
        resp = self.client.chat.completions.create(
            model=self.vision_model,
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Classify this receipt image."},
                    {"type": "image_url", "image_url": {"url": to_image_data_url(path)}},
                ]},
            ],
            temperature=0,
        )
        raw = _strip_json_fence(resp.choices[0].message.content)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"receipt_type": "unclear", "confident": False,
                    "reason": "triage response was not valid JSON"}

    # ---- Module 1 - Vision Extraction ---------------------------------------
    def extract_expense(self, source) -> dict:
        """source: a Path to an image/pdf receipt, or a plain text string."""
        if isinstance(source, (str, Path)) and Path(str(source)).exists():
            content = [
                {"type": "text", "text": "Extract the expense as JSON."},
                {"type": "image_url", "image_url": {"url": to_image_data_url(Path(source))}},
            ]
        else:
            content = [{"type": "text", "text": f"Expense note: {source}"}]

        resp = self.client.chat.completions.create(
            model=self.vision_model,
            messages=[{"role": "system", "content": MODULE1_SYSTEM},
                      *MODULE1_FEWSHOT,
                      {"role": "user", "content": content}],
            temperature=0,
        )
        raw = _strip_json_fence(resp.choices[0].message.content)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"date": None, "merchant": None, "amount": None,
                    "description": None, "_raw": raw}

    # ---- Module 2 - Query & Summary -----------------------------------------
    def resolve_relative_range(self, question: str):
        return resolve_relative_range(question, self.today)

    def answer_query(self, question: str, variant: str = "C", category=None) -> str:
        """
        variant A: bare model - no system prompt, no data, no tools.
        variant B: system prompt + full ledger dumped in; no retrieval, no tool.
        variant C: rule-based date resolution + retrieval + calculate_spend tool.
        """
        if variant == "A":
            msgs = [{"role": "user", "content": question}]
            resp = self.client.chat.completions.create(
                model=self.chat_model, messages=msgs, temperature=0)
            return resp.choices[0].message.content.strip()

        if variant == "B":
            msgs = [
                {"role": "system", "content": MODULE2_SYSTEM},
                {"role": "user", "content":
                    f"Transactions:\n{rows_to_text(self.ledger)}\n\nQuestion: {question}"},
            ]
            resp = self.client.chat.completions.create(
                model=self.chat_model, messages=msgs, temperature=0)
            return resp.choices[0].message.content.strip()

        # variant C
        start, end, note = self.resolve_relative_range(question)
        ctx = self.retrieve(start, end, category)
        header = (
            f"Today is {self.today.isoformat()}. Data available from "
            f"{self.data_min_date} to {self.data_max_date}.\n{note}\n"
        )
        range_hint = (
            f"If you call calculate_spend, use start_date=\"{start}\" and "
            f"end_date=\"{end}\" unless the question clearly needs a different range."
            if start else ""
        )
        user_msg = (
            f"{header}"
            f"Relevant transactions ({start or 'all'} to {end or 'all'}):\n"
            f"{rows_to_text(ctx)}\n\nQuestion: {question}\n{range_hint}"
        )
        msgs = [{"role": "system", "content": MODULE2_SYSTEM}, *MODULE2_FEWSHOT,
                {"role": "user", "content": user_msg}]
        for _ in range(4):
            resp = self.client.chat.completions.create(
                model=self.chat_model, messages=msgs, tools=[CALC_TOOL],
                tool_choice="auto", temperature=0,
            )
            m = resp.choices[0].message
            if not m.tool_calls:
                return (m.content or "").strip()
            msgs.append(m.model_dump(exclude_none=True))
            for tc in m.tool_calls:
                args = json.loads(tc.function.arguments)
                result = self.calculate_spend(**args)
                msgs.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result)})
        # Exhausted all rounds without a final answer - say so, don't return blank.
        return ("I wasn't able to finish answering that after several tool calls - "
                "try rephrasing the question or narrowing the date range.")
