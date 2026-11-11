"""LedgerLens: reconcile bank credits against payment-gateway settlements.

Graph: load -> match -> fees -> clarify (interrupt when credits stay unmatched) -> explain -> export.
Files and rows stay inside the container; the model only ever sees aggregates; nothing here logs file contents.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

import platform_io
import recon
import summary
import workbook

LABELS = ("refund", "capital", "other", "skip")
MAX_LISTED = 50
DEFAULT_FEE_PCT = 2.0
OUTPUT_NAME = "reconciliation.xlsx"

_CACHE: dict[str, dict] = {}  # parsed files and the reconciliation by input key; rows never enter the checkpoint


class State(TypedDict, total=False):
    bank: Any
    settlements: Any
    invoices: Any
    expected_fee_pct: Any
    status: str  # "" while running, "rejected" when the input could not be used
    message: str
    unmatched: list[dict]
    labels: dict[str, str]
    summary: dict
    output: dict


# ------------------------------------------------------------------------------------------------- helpers

def _ref(v) -> Any:
    return v if isinstance(v, dict) and (v.get("artifact") or v.get("url")) else (v if isinstance(v, str) and v else None)


def _pct(v) -> float:
    try:
        f = float(str(v).strip().rstrip("%"))
        return f if 0 <= f <= 100 else DEFAULT_FEE_PCT
    except (TypeError, ValueError):
        return DEFAULT_FEE_PCT


def _key(state: State) -> str:
    parts = []
    for name in ("bank", "settlements", "invoices"):
        r = _ref(state.get(name))
        parts.append(str(r.get("sha256") or r.get("artifact") or r.get("url")) if isinstance(r, dict) else str(r or ""))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _rejected(message: str) -> dict:
    return {"status": "rejected", "message": message}


def _entry(state: State) -> dict:
    return _CACHE.setdefault(_key(state), {})


async def _load(state: State) -> dict:
    """Parse the files once per input; raises ValueError with a message for the user."""
    e = _entry(state)
    if "bank" in e:
        return e
    bank_ref, st_ref, inv_ref = _ref(state.get("bank")), _ref(state.get("settlements")), _ref(state.get("invoices"))
    if not bank_ref or not st_ref:
        missing = " and ".join(n for n, r in (("the bank statement (bank)", bank_ref), ("the settlements report (settlements)", st_ref)) if not r)
        raise ValueError(f"I need {missing} as a CSV file to reconcile. Attach it and run again.")
    bank_bytes = await asyncio.to_thread(platform_io.fetch, bank_ref)
    st_bytes = await asyncio.to_thread(platform_io.fetch, st_ref)
    e["bank"] = recon.load_bank(bank_bytes)
    e["st"] = recon.load_settlements(st_bytes)
    e["invoices"], e["inv_warnings"] = [], []
    if inv_ref:
        inv_bytes = await asyncio.to_thread(platform_io.fetch, inv_ref)
        e["invoices"], e["inv_warnings"] = recon.load_invoices(inv_bytes)
    return e


async def _reconciled(state: State) -> recon.Recon:
    """The reconciliation, recomputed from the files when the cache is cold (e.g. after a restart)."""
    e = await _load(state)
    if "rec" not in e:
        e["rec"] = recon.reconcile_best(e["bank"], e["st"], e["invoices"], e["inv_warnings"])
    return e["rec"]


def _unmatched_view(rec: recon.Recon) -> list[dict]:
    return [{"id": c.id, "row": c.row, "date": c.date.isoformat() if c.date else "", "amount": c.amount / 100,
             "description": c.desc[:120]} for c in rec.unmatched_credits]


def normalize_labels(reply: Any, ids: list[str]) -> dict[str, str]:
    """Labels by credit id from whatever the user sent: a {id: label} dict, one label for all, or JSON text."""
    if isinstance(reply, str):
        t = reply.strip()
        if t.lower() in LABELS:
            reply = t.lower()
        else:
            try:
                reply = json.loads(t)
            except ValueError:
                reply = "skip"
    if isinstance(reply, str):
        return {i: reply.lower() for i in ids} if reply.lower() in LABELS else {}
    if isinstance(reply, list):
        reply = {x.get("id"): x.get("label") for x in reply if isinstance(x, dict)}
    out: dict[str, str] = {}
    if isinstance(reply, dict):
        for i in ids:
            v = reply.get(i)
            if isinstance(v, str) and v.strip().lower() in LABELS:
                out[i] = v.strip().lower()
    return out


# ------------------------------------------------------------------------------------------------- nodes

async def load_node(state: State) -> dict:
    try:
        e = await _load(state)
    except ValueError as exc:
        return _rejected(str(exc))
    b = e["bank"]
    return {"status": "", "message": f"Read {len(b.credits)} bank credits and {len(e['st'].payouts)} settlement payouts."}


async def match_node(state: State) -> dict:
    if state.get("status") == "rejected":
        return {}
    rec = await _reconciled(state)
    return {"unmatched": _unmatched_view(rec),
            "message": f"Matched {sum(len(m.credits) for m in rec.matches)} of {len(rec.bank.credits)} credits."}


async def fees_node(state: State) -> dict:
    if state.get("status") == "rejected":
        return {}
    rec = await _reconciled(state)
    fees = recon.compute_fees(rec.payouts, _pct(state.get("expected_fee_pct")), rec.currency)
    flagged = sum(len(f.flagged) for f in fees)
    return {"message": f"Fees computed for {len(fees)} gateways; {flagged} payouts above the expected rate."}


def clarify_node(state: State) -> dict:
    items = state.get("unmatched") or []
    if state.get("status") == "rejected" or not items:
        return {}
    reply = interrupt({
        "question": f"{len(items)} bank credits match no settlement. Label each as refund, capital, other, or skip.",
        "allowed": list(LABELS),
        "format": 'Reply with {"<id>": "<label>"} for each credit, or a single label to apply to all.',
        "items": items[:MAX_LISTED],
        "more": max(0, len(items) - MAX_LISTED),
    })
    labels = normalize_labels(reply, [i["id"] for i in items])
    return {"labels": {k: v for k, v in labels.items() if v != "skip"}}


async def explain_node(state: State) -> dict:
    if state.get("status") == "rejected":
        return {}
    rec = await _reconciled(state)
    agg = workbook.aggregates(rec, state.get("labels") or {}, _pct(state.get("expected_fee_pct")))
    text, _ = await asyncio.to_thread(summary.explain, agg, platform_io.ask_model)
    return {"summary": summary.unmask(text, workbook.gateway_names(rec, _pct(state.get("expected_fee_pct"))))}


async def export_node(state: State) -> dict:
    if state.get("status") == "rejected":
        return {"message": state.get("message", "")}
    rec = await _reconciled(state)
    pct = _pct(state.get("expected_fee_pct"))
    text = state.get("summary") or summary.unmask(
        summary.template(workbook.aggregates(rec, state.get("labels") or {}, pct)), workbook.gateway_names(rec, pct))
    data = workbook.build(rec, state.get("labels") or {}, text, _pct(state.get("expected_fee_pct")))
    out = await asyncio.to_thread(platform_io.put_output, OUTPUT_NAME, data)
    lines = [text["headline"], *text["findings"]]
    if text.get("next_steps"):
        lines += ["Next steps:", *text["next_steps"]]
    lines.append(f"The full reconciliation is in {OUTPUT_NAME}.")
    _CACHE.pop(_key(state), None)
    return {"status": "done", "message": "\n".join(lines),
            "output": {"name": OUTPUT_NAME, "artifact": (out or {}).get("id") or (out or {}).get("artifact", "")}}


def _after_load(state: State) -> str:
    return "end" if state.get("status") == "rejected" else "match"


def build_graph(checkpointer=None):
    g = StateGraph(State)
    for name, fn in (("load", load_node), ("match", match_node), ("fees", fees_node), ("clarify", clarify_node),
                     ("explain", explain_node), ("export", export_node)):
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_conditional_edges("load", _after_load, {"end": "export", "match": "match"})
    g.add_edge("match", "fees")
    g.add_edge("fees", "clarify")
    g.add_edge("clarify", "explain")
    g.add_edge("explain", "export")
    g.add_edge("export", END)
    return g.compile(checkpointer=checkpointer)


graph = build_graph()
