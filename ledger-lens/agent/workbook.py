"""The reconciliation workbook: Summary, Matched, Unmatched and Fees, colour coded.

Green is a confident match, amber a fuzzy one (worth a look) and red something unmatched or overcharged.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from recon import GatewayFees, Recon, compute_fees, payout_fee_pct, split_fee

GREEN = "C6EFCE"
AMBER = "FFEB9C"
RED = "FFC7CE"
HEAD = "D9D9D9"
FILL = {"matched": GREEN, "fuzzy": AMBER, "unmatched": RED}
MONEY = '#,##0.00'
KIND_LABEL = {
    "reference": "Reference", "reference_batch": "Reference (several credits)", "amount_date": "Amount and date",
    "batch": "Batch of credits", "batch_gross": "Batch of credits (before fees)", "payout_group": "One credit, several payouts",
    "invoice_ref": "Invoice number", "invoice_amount": "Invoice amount",
}


def fill(color: str) -> PatternFill:
    return PatternFill(start_color=color, end_color=color, fill_type="solid")


def _put(ws, row: int, col: int, value, money: bool = False, color: str | None = None, bold: bool = False):
    cell = ws.cell(row=row, column=col)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"  # a description that starts with = or @ is text, never a formula
    if money and isinstance(value, (int, float)):
        cell.number_format = MONEY
    if color:
        cell.fill = fill(color)
    if bold:
        cell.font = Font(bold=True)
    cell.alignment = Alignment(vertical="top", wrap_text=isinstance(value, str) and len(value) > 40)
    return cell


def _header(ws, headers: list[str]) -> None:
    for i, h in enumerate(headers, 1):
        _put(ws, 1, i, h, color=HEAD, bold=True)
    ws.freeze_panes = "A2"


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _major(minor: int | None) -> float | None:
    return None if minor is None else minor / 100


MAX_GATEWAYS = 10


def gateway_names(rec: Recon, expected_pct: float = 2.0) -> dict[str, str]:
    """"Gateway 1" -> the name in the file. The model only ever sees the labels; the workbook gets the names back."""
    fees = compute_fees(rec.payouts, expected_pct, rec.currency)
    return {f"Gateway {i}": " ".join(str(f.gateway).split())[:40] or f"Gateway {i}" for i, f in enumerate(fees[:MAX_GATEWAYS], 1)}


def aggregates(rec: Recon, labels: dict[str, str] | None = None, expected_pct: float = 2.0) -> dict:
    """The numbers of a reconciliation: counts and totals only, safe to show a model."""
    labels = labels or {}
    credits = rec.bank.credits
    matched_ids = {c for m in rec.matches for c in m.credits}
    by_status = {"matched": [0, 0], "fuzzy": [0, 0]}
    for m in rec.matches:
        amt = sum(rec.credit(c).amount for c in m.credits)
        by_status[m.status][0] += len(m.credits)
        by_status[m.status][1] += amt
    fees = compute_fees(rec.payouts, expected_pct, rec.currency)
    by_label: dict[str, list[int]] = {}
    for c in rec.unmatched_credits:
        slot = by_label.setdefault(labels.get(c.id, "unlabelled"), [0, 0])
        slot[0] += 1
        slot[1] += c.amount
    return {
        "currency": rec.currency,
        "bank_credits": {"count": len(credits), "total": sum(c.amount for c in credits)},
        "bank_debits": {"count": len(rec.bank.debits), "total": sum(c.amount for c in rec.bank.debits)},
        "matched": {"count": by_status["matched"][0], "total": by_status["matched"][1]},
        "fuzzy": {"count": by_status["fuzzy"][0], "total": by_status["fuzzy"][1]},
        "unmatched": {"count": len(rec.unmatched_credits), "total": sum(c.amount for c in rec.unmatched_credits)},
        "explained_credit_count": len(matched_ids),
        "payouts": {"count": len(rec.payouts), "total": sum(p.net for p in rec.payouts)},
        "missing_payouts": {"count": len(rec.unmatched_payouts), "total": sum(p.net for p in rec.unmatched_payouts)},
        "labels": {k: {"count": v[0], "total": v[1]} for k, v in sorted(by_label.items())},
        "fees": [{"gateway": f"Gateway {i}", "payouts": f.payouts, "gross": f.gross, "fee": f.fee, "tax": f.tax,
                  "pct": f.pct, "all_in_pct": f.all_in_pct, "expected_pct": f.expected_pct,
                  "flagged_payouts": len(f.flagged), "overcharge": f.overcharge} for i, f in enumerate(fees[:MAX_GATEWAYS], 1)],
        "more_gateways": max(0, len(fees) - MAX_GATEWAYS),
        "warnings": len(rec.warnings),
    }


def build(rec: Recon, labels: dict[str, str] | None, summary: dict, expected_pct: float = 2.0) -> bytes:
    """The .xlsx bytes. `summary` is {"headline", "findings", "next_steps"} (model or template)."""
    labels = labels or {}
    cur = rec.currency
    wb = Workbook()

    # Summary
    ws = wb.active
    ws.title = "Summary"
    agg = aggregates(rec, labels, expected_pct)
    _put(ws, 1, 1, "LedgerLens reconciliation", bold=True)
    _put(ws, 2, 1, summary.get("headline", ""))
    ws.merge_cells("A2:D2")
    r = 4
    _header_row = ["Measure", "Count", f"Amount ({cur or 'currency'})"]
    for i, h in enumerate(_header_row, 1):
        _put(ws, r, i, h, color=HEAD, bold=True)
    rows = [
        ("Bank credits", agg["bank_credits"], None),
        ("Matched (reference, exact)", agg["matched"], GREEN),
        ("Fuzzy matches (check these)", agg["fuzzy"], AMBER),
        ("Unmatched credits", agg["unmatched"], RED if agg["unmatched"]["count"] else GREEN),
        ("Settlement payouts", agg["payouts"], None),
        ("Payouts missing from the bank", agg["missing_payouts"], RED if agg["missing_payouts"]["count"] else GREEN),
        ("Bank debits", agg["bank_debits"], None),
    ]
    for label, v, color in rows:
        r += 1
        _put(ws, r, 1, label, color=color)
        _put(ws, r, 2, v["count"], color=color)
        _put(ws, r, 3, _major(v["total"]), money=True, color=color)
    if agg["labels"]:
        r += 2
        _put(ws, r, 1, "Unmatched credits, as you labelled them", bold=True)
        for name, v in agg["labels"].items():
            r += 1
            _put(ws, r, 1, name)
            _put(ws, r, 2, v["count"])
            _put(ws, r, 3, _major(v["total"]), money=True)
    r += 2
    _put(ws, r, 1, "Findings", bold=True)
    for line in summary.get("findings", []):
        r += 1
        _put(ws, r, 1, line)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    if summary.get("next_steps"):
        r += 2
        _put(ws, r, 1, "Next steps", bold=True)
        for line in summary["next_steps"]:
            r += 1
            _put(ws, r, 1, line)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    if rec.warnings:
        r += 2
        _put(ws, r, 1, "Notes about your files", bold=True)
        for line in rec.warnings:
            r += 1
            _put(ws, r, 1, line)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    _widths(ws, [44, 12, 22, 40])

    # Matched
    ws = wb.create_sheet("Matched")
    _header(ws, ["Status", "Match type", "Bank row", "Bank date", "Bank amount", "Bank description", "Bank reference",
                 "Settlement", "Settlement date", "Settlement net", "Gateway", "Difference", "Note"])
    payouts = {p.id: p for p in rec.payouts}
    r = 1
    for m in rec.matches:
        color = FILL[m.status]
        ps = [payouts[p] for p in m.payouts]
        pdate = max((p.date for p in ps if p.date), default=None)
        for i, cid in enumerate(m.credits):
            c = rec.credit(cid)
            r += 1
            first = i == 0
            _put(ws, r, 1, "Matched" if m.status == "matched" else "Fuzzy", color=color)
            _put(ws, r, 2, KIND_LABEL.get(m.kind, m.kind), color=color)
            _put(ws, r, 3, c.row, color=color)
            _put(ws, r, 4, c.date.isoformat() if c.date else "", color=color)
            _put(ws, r, 5, _major(c.amount), money=True, color=color)
            _put(ws, r, 6, c.desc, color=color)
            _put(ws, r, 7, c.ref, color=color)
            _put(ws, r, 8, ", ".join(p.ref or p.id for p in ps) if first else "", color=color)
            _put(ws, r, 9, pdate.isoformat() if pdate and first else "", color=color)
            _put(ws, r, 10, _major(sum(p.net for p in ps)) if ps and first else None, money=True, color=color)
            _put(ws, r, 11, ", ".join(sorted({p.gateway for p in ps})) if first else "", color=color)
            _put(ws, r, 12, _major(m.diff) if first and m.diff else None, money=True, color=color)
            _put(ws, r, 13, m.note if first else "", color=color)
    _widths(ws, [10, 28, 9, 12, 16, 48, 26, 26, 14, 16, 14, 14, 50])

    # Unmatched
    ws = wb.create_sheet("Unmatched")
    _header(ws, ["Kind", "Row", "Date", "Amount", "Description", "Reference", "Your label"])
    r = 1
    for c in rec.unmatched_credits:
        r += 1
        for col, v in enumerate(["Bank credit", c.row, c.date.isoformat() if c.date else "", _major(c.amount), c.desc, c.ref,
                                 labels.get(c.id, "")], 1):
            _put(ws, r, col, v, money=col == 4, color=RED)
    for p in rec.unmatched_payouts:
        r += 1
        for col, v in enumerate(["Settlement not in bank", p.row, p.date.isoformat() if p.date else "", _major(p.net),
                                 p.gateway, p.ref, ""], 1):
            _put(ws, r, col, v, money=col == 4, color=RED)
    _widths(ws, [24, 8, 12, 16, 56, 28, 16])

    # Fees
    ws = wb.create_sheet("Fees")
    _header(ws, ["Gateway", "Settlement", "Date", "Gross", "Fee (excl. GST)", "GST", "Net", "Fee % (excl. GST)", "Expected %", "Over expected"])
    r = 1
    fees: list[GatewayFees] = compute_fees(rec.payouts, expected_pct, rec.currency)
    for f in fees:
        r += 1
        color = RED if f.flagged else GREEN
        vals = [f.gateway, f"{f.payouts} payouts", "", _major(f.gross), _major(f.fee), _major(f.tax), _major(f.net), f.pct,
                f.expected_pct, _major(f.overcharge) if f.overcharge else None]
        for col, v in enumerate(vals, 1):
            _put(ws, r, col, v, money=col in (4, 5, 6, 7, 10), color=color, bold=True)
    flagged_ids = {p for f in fees for p in f.flagged}
    for p in rec.payouts:
        pct = payout_fee_pct(p, rec.currency)
        if pct is None:
            continue
        r += 1
        color = RED if p.id in flagged_ids else None
        fee, tax = split_fee(p, rec.currency)
        vals = [p.gateway, p.ref or p.id, p.date.isoformat() if p.date else "", _major(p.gross), _major(fee),
                _major(tax), _major(p.net), pct, expected_pct, None]
        for col, v in enumerate(vals, 1):
            _put(ws, r, col, v, money=col in (4, 5, 6, 7), color=color)
    _widths(ws, [16, 28, 12, 16, 14, 14, 16, 10, 12, 16])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
