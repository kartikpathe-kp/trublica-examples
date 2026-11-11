"""Reconciliation of a bank statement against gateway settlements (and, optionally, invoices).

Everything here is deterministic and offline. Amounts are integers in minor units (paise, cents); a credit is positive.
Nothing in this module logs or prints: file contents never leave the data structures.
"""

from __future__ import annotations

import io
import bisect
import itertools
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from parsing import (currency_of, detect_date_order, extract_refs, norm_header, norm_ref, parse_amount, parse_date,
                     read_rows, refs_overlap)

DATE_WINDOW_DAYS = 3
BATCH_WINDOW_DAYS = 5
MAX_SUBSET = 6
MAX_CANDIDATES = 22
MAX_STEPS = 300_000
MAX_TOTAL_STEPS = 3_000_000
INVOICE_WINDOW_DAYS = 60
FEE_TOLERANCE_PCT = 0.005


# ------------------------------------------------------------------------------------------------- data classes

@dataclass
class Txn:
    id: str
    row: int
    date: date | None
    amount: int  # credit > 0, debit < 0
    desc: str
    ref: str
    refs: frozenset[str] = frozenset()


@dataclass
class Payout:
    id: str
    row: int
    date: date | None
    net: int
    gross: int | None
    fee: int | None
    tax: int | None
    ref: str
    gateway: str
    refs: frozenset[str] = frozenset()
    lines: int = 1


@dataclass
class Invoice:
    id: str
    number: str
    date: date | None
    amount: int
    source: str = ""


@dataclass
class Bank:
    credits: list[Txn]
    debits: list[Txn]
    currency: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Settlements:
    payouts: list[Payout]
    alt: list[Payout] | None = None  # the reading where the lone amount column is net, when that is ambiguous
    currency: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Match:
    kind: str  # reference, reference_batch, amount_date, batch, batch_gross, payout_group, invoice_ref, invoice_amount
    status: str  # matched (green) or fuzzy (amber)
    credits: list[str]
    payouts: list[str]
    diff: int = 0  # bank minus settlement, minor units
    note: str = ""


@dataclass
class Recon:
    bank: Bank
    payouts: list[Payout]
    invoices: list[Invoice]
    matches: list[Match]
    unmatched_credits: list[Txn]
    unmatched_payouts: list[Payout]
    currency: str = ""
    warnings: list[str] = field(default_factory=list)
    _by_id: dict = field(default_factory=dict, repr=False)

    def credit(self, cid: str) -> Txn:
        if len(self._by_id) != len(self.bank.credits):
            self._by_id = {c.id: c for c in self.bank.credits}
        return self._by_id[cid]


# ------------------------------------------------------------------------------------------------- header matching

def _classify_bank(h: str) -> tuple[str, int]:
    n = norm_header(h)
    if not n:
        return "", 0
    if re.search(r"\bbal(ance)?\b", n):
        return "balance", 9
    if n in {"dr cr", "cr dr", "type", "txn type", "transaction type", "debit credit", "credit debit", "dr or cr",
             "cr or dr", "indicator", "drcr", "crdr", "dr cr flag", "credit debit indicator"}:
        return "type", 5
    if re.search(r"\b(debit|debits|dr|withdrawal|withdrawals|withdrawn|paid out|money out|outflow)\b", n):
        return "debit", 5
    if re.search(r"\b(credit|credits|cr|deposit|deposits|paid in|money in|inflow|receipt|receipts)\b", n):
        return "credit", 5
    if re.search(r"\b(txn|transaction|tran|trans|posting|post|booking)\b.*\b(date|dt)\b", n) or n in {"date", "dt"}:
        return "date", 4
    if re.search(r"\bvalue (date|dt)\b", n):
        return "date", 2
    if re.search(r"\b(date|dt)\b", n):
        return "date", 1
    if re.search(r"\b(utr|rrn)\b", n):
        return "ref", 5
    if re.search(r"\b(ref|reference)\b", n) or re.search(r"\b(txn|transaction|trans|tran|payment) (id|no|number)\b", n):
        return "ref", 4
    if re.search(r"\b(chq|cheque|check|instrument)\b", n):
        return "ref", 2
    if re.search(r"\b(narration|particulars|description|remarks|details|memo|narrative)\b", n):
        return "desc", 4
    if re.search(r"\b(amount|amt|value|sum)\b", n):
        return "amount", 3
    return "", 0


def _classify_settlement(h: str) -> tuple[str, int]:
    n = norm_header(h)
    if not n:
        return "", 0
    if re.search(r"\b(date|dt|settled at|created at|paid at|processed at|time|settled on|paid on)\b", n):
        return "date", 4
    if re.search(r"(count|no of|number of|num of|\bqty\b|quantity)", n):
        return "count", 5
    if re.search(r"\b(utr|rrn)\b", n):
        return "ref", 6
    if re.search(r"\b(id|ref|reference|batch|payout no|settlement no|number)\b", n):
        return "ref", 4
    if re.search(r"\b(tax|gst|igst|cgst|sgst|vat)\b", n):
        return "tax", 5
    if re.search(r"\b(fee|fees|commission|mdr|charges?|deduction|deductions)\b", n):
        return "fee", 5
    if re.search(r"\b(net|settled|settlement|payout|payable|credited|received)\b", n):
        return "net", 5
    if re.search(r"\b(gross|total|payment|payments|sales|collection|collected|amount|amt)\b", n):
        return "gross", 3 if re.search(r"\b(amount|amt)\b", n) else 5
    if re.search(r"\b(gateway|provider|processor|psp|channel|platform|source|acquirer)\b", n):
        return "gateway", 5
    if re.search(r"\b(description|narration|remarks|notes?)\b", n):
        return "desc", 3
    return "", 0


def _classify_invoice(h: str) -> tuple[str, int]:
    n = norm_header(h)
    if re.search(r"\b(date|dt)\b", n):
        return "date", 4
    if re.search(r"\b(invoice|inv|bill)\b.*\b(no|number|id|num)\b|^(invoice|inv|bill)$", n):
        return "number", 5
    if re.search(r"\b(total|grand total|amount|amt|payable|value)\b", n):
        return "amount", 4 if "total" in n else 3
    return "", 0


def _pick_columns(header: list[str], classify) -> dict[str, int]:
    best: dict[str, tuple[int, int]] = {}
    for i, h in enumerate(header):
        kind, score = classify(h)
        if kind and (kind not in best or score > best[kind][0]):
            best[kind] = (score, i)
    return {k: i for k, (s, i) in best.items()}


def _find_header(rows: list[list[str]], classify, needs: set[str], what: str) -> tuple[int, dict[str, int]]:
    """The header row: the first within the top 40 rows that names the needed columns and at least one more kind
    (statements often start with a few lines of account details)."""
    for i, row in enumerate(rows[:40]):
        cols = _pick_columns(row, classify)
        if needs <= set(cols) and len(cols) >= len(needs) + (1 if len(needs) == 1 else 0):
            return i, cols
    raise ValueError(f"I couldn't find the columns I need in the {what} file. " + _expected(what))


def _expected(what: str) -> str:
    return {
        "bank": "It needs a date column, an amount column (or separate debit and credit columns) and ideally a "
                "description or reference column. Check that you attached the bank statement as the bank file.",
        "settlements": "It needs a date column and an amount column (and ideally the settlement id or UTR and the fee). "
                       "Check that you attached the gateway settlement report as the settlements file.",
        "invoices": "It needs an invoice number, a date and a total amount.",
    }[what]


def _cell(row: list[str], i: int | None) -> str:
    return row[i] if i is not None and i < len(row) else ""


# ------------------------------------------------------------------------------------------------- bank

def load_bank(data: bytes) -> Bank:
    rows = read_rows(data, "bank")
    hi, cols = _find_header(rows, _classify_bank, {"date"}, "bank")
    if not ({"amount"} <= set(cols) or {"credit"} <= set(cols) or {"debit"} <= set(cols)):
        raise ValueError("I couldn't find an amount in the bank file: it needs an amount column or separate debit and "
                         "credit columns. " + _expected("bank"))
    body = rows[hi + 1:]
    order = detect_date_order([_cell(r, cols["date"]) for r in body])
    credits, debits, warnings = [], [], []
    skipped = bad_amount = no_type = 0
    seen_currency: list[str] = []
    for n, row in enumerate(body, start=hi + 2):
        d = parse_date(_cell(row, cols["date"]), order)
        if d is None:
            skipped += 1
            continue
        amount = None
        try:
            if "credit" in cols or "debit" in cols:
                cr, _ = parse_amount(_cell(row, cols.get("credit")))
                dr, _ = parse_amount(_cell(row, cols.get("debit")))
                if cr:
                    amount = abs(cr)
                elif dr:
                    amount = -abs(dr)
                if "amount" in cols and amount is None:
                    amount, mk = parse_amount(_cell(row, cols["amount"]))
            else:
                amount, mk = parse_amount(_cell(row, cols["amount"]))
                kind = _cell(row, cols.get("type")).strip().lower()
                if amount is not None:
                    if mk == "dr" or kind in {"dr", "d", "debit", "withdrawal"}:
                        amount = -abs(amount)
                    elif mk == "cr" or kind in {"cr", "c", "credit", "deposit"}:
                        amount = abs(amount)
                    elif "type" not in cols and amount > 0:
                        no_type += 1
        except ValueError:
            bad_amount += 1
            continue
        if not amount:
            continue
        for key in ("credit", "debit", "amount"):
            if key in cols:
                seen_currency.append(currency_of(_cell(row, cols[key])))
        desc = _cell(row, cols.get("desc"))
        ref = _cell(row, cols.get("ref"))
        t = Txn(id="", row=n, date=d, amount=amount, desc=desc, ref=ref, refs=extract_refs(desc, ref))
        (credits if amount > 0 else debits).append(t)
    if not credits and not debits:
        raise ValueError("I found no transactions in the bank file: no row has both a valid date and an amount. "
                         + _expected("bank"))
    if skipped:
        warnings.append(f"{skipped} bank rows without a readable date were skipped (headers, totals or notes).")
    if bad_amount:
        warnings.append(f"{bad_amount} bank rows had an amount I could not read and were skipped.")
    if no_type and "debit" not in cols and "credit" not in cols and not debits:
        warnings.append("The bank file has one amount column and no Dr/Cr column, so every amount was treated as a credit.")
    for i, t in enumerate(credits, 1):
        t.id = f"C{i}"
    for i, t in enumerate(debits, 1):
        t.id = f"D{i}"
    cur = next((c for c in seen_currency if c), "")
    return Bank(credits, debits, cur, warnings)


# ------------------------------------------------------------------------------------------------- settlements

def guess_gateway(ref: str) -> str:
    low = ref.lower()
    if low.startswith(("setl_", "pout_", "razorpay")):
        return "Razorpay"
    if low.startswith(("po_", "payout_")):
        return "Stripe"
    return ""


def load_settlements(data: bytes) -> Settlements:
    rows = read_rows(data, "settlements")
    hi, cols = _find_header(rows, _classify_settlement, {"date"}, "settlements")
    if not ({"gross", "net"} & set(cols)):
        raise ValueError("I couldn't find an amount in the settlements file. " + _expected("settlements"))
    body = rows[hi + 1:]
    order = detect_date_order([_cell(r, cols["date"]) for r in body])
    ambiguous = "net" not in cols and "gross" in cols and bool({"fee", "tax"} & set(cols))
    skipped = bad = 0
    raw: list[dict] = []
    currency = ""
    for n, row in enumerate(body, start=hi + 2):
        d = parse_date(_cell(row, cols["date"]), order)
        if d is None:
            skipped += 1
            continue
        try:
            vals = {k: parse_amount(_cell(row, cols.get(k)))[0] for k in ("gross", "net", "fee", "tax")}
        except ValueError:
            bad += 1
            continue
        if vals["gross"] is None and vals["net"] is None:
            continue
        for k in ("gross", "net"):
            currency = currency or currency_of(_cell(row, cols.get(k)))
        ref = _cell(row, cols.get("ref"))
        gw = _cell(row, cols.get("gateway")) or guess_gateway(ref) or "Gateway"
        count = 1
        try:
            count = int(float(_cell(row, cols.get("count")) or 1))
        except ValueError:
            pass
        raw.append({"row": n, "date": d, "ref": ref, "gateway": gw, "count": max(count, 1),
                    "desc": _cell(row, cols.get("desc")), **vals})
    if not raw:
        raise ValueError("I found no settlements in the settlements file: no row has both a valid date and an amount. "
                         + _expected("settlements"))
    warnings = []
    if skipped:
        warnings.append(f"{skipped} settlement rows without a readable date were skipped.")
    if bad:
        warnings.append(f"{bad} settlement rows had an amount I could not read and were skipped.")
    primary = _payouts(raw, lone_is_net=("net" not in cols and not ({"fee", "tax"} & set(cols))))
    alt = _payouts(raw, lone_is_net=True) if ambiguous else None
    return Settlements(primary, alt, currency, warnings)


def _payouts(raw: list[dict], lone_is_net: bool) -> list[Payout]:
    """Group rows that share a settlement reference (payment-level reports) into payouts, then derive net/gross."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for i, r in enumerate(raw):
        key = norm_ref(r["ref"]) or f"__row{i}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    out: list[Payout] = []
    for key in order:
        rs = groups[key]

        def tot(field_: str):
            vals = [r[field_] for r in rs if r[field_] is not None]
            return sum(vals) if vals else None

        gross, net, fee, tax = tot("gross"), tot("net"), tot("fee"), tot("tax")
        if net is None and gross is not None:
            if lone_is_net:
                net, gross = gross, None
                if fee is not None or tax is not None:
                    gross = net + (fee or 0) + (tax or 0)
            else:
                net = gross - (fee or 0) - (tax or 0)
        elif gross is None and net is not None and (fee is not None or tax is not None):
            gross = net + (fee or 0) + (tax or 0)
        if net is None:
            continue
        ref = rs[0]["ref"]
        refs = extract_refs(*[r["ref"] for r in rs[:1]], *[r["desc"] for r in rs[:3]])
        out.append(Payout(id="", row=rs[0]["row"], date=max(r["date"] for r in rs), net=net, gross=gross, fee=fee, tax=tax,
                          ref=ref, gateway=rs[0]["gateway"], refs=refs, lines=sum(r["count"] for r in rs)))
    out.sort(key=lambda p: (p.date or date.min, p.row))
    for i, p in enumerate(out, 1):
        p.id = f"S{i}"
    return out


# ------------------------------------------------------------------------------------------------- invoices

_INV_NO = re.compile(r"(?:invoice|inv|bill)\s*(?:no\.?|number|num|#|id)?\s*[:\-#]?\s*([A-Z0-9][A-Z0-9/\-_.]{2,24})", re.I)
_TOTAL = re.compile(r"(?:grand\s+total|total\s+amount|amount\s+due|net\s+payable|total\s+payable|invoice\s+total|total)"
                    r"[^0-9₹\n]{0,25}(?:₹|rs\.?|inr|\$)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
_DATE_IN_TEXT = re.compile(r"(\d{1,2}[-/. ](?:\d{1,2}|[A-Za-z]{3,9})[-/. ,]*\d{2,4}|\d{4}-\d{2}-\d{2})")
MAX_PDFS = 300
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_ZIP_TOTAL = 80 * 1024 * 1024


def load_invoices(data: bytes) -> tuple[list[Invoice], list[str]]:
    """Invoices from a CSV, or from a ZIP of PDFs (invoice number, date and total are read from each PDF's text)."""
    if data[:2] == b"PK":
        return _invoices_from_zip(data)
    rows = read_rows(data, "invoices")
    hi, cols = _find_header(rows, _classify_invoice, {"number", "amount"}, "invoices")
    body = rows[hi + 1:]
    order = detect_date_order([_cell(r, cols.get("date")) for r in body])
    out, bad = [], 0
    for r in body:
        num = _cell(r, cols["number"]).strip()
        try:
            amt, _ = parse_amount(_cell(r, cols["amount"]))
        except ValueError:
            bad += 1
            continue
        if not num or amt is None:
            continue
        out.append(Invoice(f"I{len(out) + 1}", num, parse_date(_cell(r, cols.get("date")), order), abs(amt)))
    if not out:
        raise ValueError("I found no invoices in the invoices file. " + _expected("invoices"))
    return out, ([f"{bad} invoice rows had an amount I could not read."] if bad else [])


def _invoices_from_zip(data: bytes) -> tuple[list[Invoice], list[str]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".pdf")
                 and not i.filename.startswith("__MACOSX")]
    except zipfile.BadZipFile:
        raise ValueError("The invoices file is not a valid zip. Attach a CSV, or a zip of PDF invoices.") from None
    if not infos:
        raise ValueError("The invoices zip has no PDF files in it.")
    warnings: list[str] = []
    if len(infos) > MAX_PDFS:
        warnings.append(f"Only the first {MAX_PDFS} of {len(infos)} invoice PDFs were read.")
        infos = infos[:MAX_PDFS]
    if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
        raise ValueError("The invoices zip is too large once unpacked (over 80 MB).")
    out, unreadable = [], 0
    for info in infos:
        if info.file_size > MAX_PDF_BYTES:
            unreadable += 1
            continue
        inv = parse_invoice_pdf(zf.read(info), f"I{len(out) + 1}", info.filename.rsplit("/", 1)[-1])
        if inv is None:
            unreadable += 1
        else:
            out.append(inv)
    if unreadable:
        warnings.append(f"{unreadable} invoice PDFs had no readable invoice number and total (scanned images?) and were skipped.")
    if not out:
        raise ValueError("I couldn't read an invoice number and total from any PDF in the invoices zip. "
                         "Scanned images are not supported; attach a CSV of invoices instead.")
    return out, warnings


def parse_invoice_pdf(data: bytes, inv_id: str, filename: str = "") -> Invoice | None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:5])
    except Exception:  # noqa: BLE001 - a broken PDF is skipped, never fatal
        return None
    if not text.strip():
        return None
    totals = _TOTAL.findall(text)
    amount = None
    for t in reversed(totals):  # the last "total" is normally the grand total
        try:
            amount, _ = parse_amount(t)
        except ValueError:
            continue
        if amount:
            break
    num = ""
    for m in _INV_NO.finditer(text):
        cand = m.group(1).strip(".-/")
        if re.search(r"\d", cand):
            num = cand
            break
    if not num:
        num = re.sub(r"\.pdf$", "", filename, flags=re.I)
    if not amount:
        return None
    d = None
    around = re.search(r"date[^0-9A-Za-z]{0,6}" + _DATE_IN_TEXT.pattern, text, re.I)
    m = _DATE_IN_TEXT.search(around.group(0)) if around else _DATE_IN_TEXT.search(text)
    if m:
        d = parse_date(m.group(1))
    return Invoice(inv_id, num, d, abs(amount), filename)


# ------------------------------------------------------------------------------------------------- matching

def _days(a: date | None, b: date | None) -> int | None:
    return None if a is None or b is None else abs((a - b).days)


class Budget:
    """A shared cap on subset-search work, so a huge file cannot make matching run away."""

    def __init__(self, steps: int = MAX_TOTAL_STEPS) -> None:
        self.left = steps


def _subsets(items: list, target: int, key, lo: int = 2, hi: int = MAX_SUBSET, budget: Budget | None = None) -> list[tuple]:
    """Subsets of 2..6 items whose amounts sum to the target. Positive amounts only; bounded work."""
    if budget is not None and budget.left <= 0:
        return []
    items = sorted(items, key=key, reverse=True)[:MAX_CANDIDATES]
    found: list[tuple] = []
    steps = 0

    def walk(start: int, chosen: list, total: int) -> None:
        nonlocal steps
        steps += 1
        if steps > MAX_STEPS or len(found) >= 25:
            return
        if total == target and len(chosen) >= lo:
            found.append(tuple(chosen))
            return
        if len(chosen) >= hi:
            return
        for i in range(start, len(items)):
            v = key(items[i])
            if v <= 0 or total + v > target:
                continue
            chosen.append(items[i])
            walk(i + 1, chosen, total + v)
            chosen.pop()

    walk(0, [], 0)
    if budget is not None:
        budget.left -= steps
    return found


def _spread(dates: list[date | None]) -> int:
    ds = [d for d in dates if d]
    return (max(ds) - min(ds)).days if ds else 9999


def _credit_mid(cs: tuple[Txn, ...], p: Payout) -> int:
    return sum(_days(c.date, p.date) or 0 for c in cs)


def _tail_keys(refs) -> set[str]:
    keys = set(refs)
    keys.update("~" + t[-12:] for t in refs if len(t) >= 12)  # a UTR carrying a bank prefix shares its last 12 characters
    return keys


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^A-Z0-9]+", (text or "").upper()) if t]


def _by_date(items: list) -> tuple[list[int], list]:
    dated = sorted((x for x in items if x.date), key=lambda x: (x.date.toordinal(), x.row))
    return [x.date.toordinal() for x in dated], dated


def _window(index: tuple[list[int], list], day: date, days: int) -> list:
    keys, items = index
    lo = bisect.bisect_left(keys, day.toordinal() - days)
    hi = bisect.bisect_right(keys, day.toordinal() + days)
    return items[lo:hi]


def reconcile(bank: Bank, payouts: list[Payout], invoices: list[Invoice] | None = None, currency: str = "") -> Recon:
    """Match credits to payouts and invoices. Credits are indexed by reference, amount and date, so a large file is
    matched in roughly n log n; the subset search shares one work budget."""
    credits = bank.credits
    free_c = {c.id: c for c in credits}
    free_p = {p.id: p for p in payouts}
    matches: list[Match] = []
    budget = Budget()

    ref_index: dict[str, list[Txn]] = {}
    amount_index: dict[int, list[Txn]] = {}
    for c in credits:
        for k in _tail_keys(c.refs):
            ref_index.setdefault(k, []).append(c)
        amount_index.setdefault(c.amount, []).append(c)
    credit_dates = _by_date(credits)
    payout_dates = _by_date(payouts)

    def take(m: Match) -> None:
        for c in m.credits:
            free_c.pop(c, None)
        for p in m.payouts:
            free_p.pop(p, None)
        matches.append(m)

    ordered = sorted(payouts, key=lambda p: (p.date or date.min, p.row))

    # 1. exact reference: a UPI / NEFT / UTR / settlement id found on both sides
    for p in ordered:
        if p.id not in free_p or not p.refs:
            continue
        seen: dict[str, Txn] = {}
        for k in _tail_keys(p.refs):
            for c in ref_index.get(k, ()):
                if c.id in free_c and c.id not in seen and refs_overlap(p.refs, c.refs):
                    seen[c.id] = c
        cands = sorted(seen.values(), key=lambda c: c.row)
        if not cands:
            continue
        exact = [c for c in cands if c.amount == p.net]
        if exact:
            c = min(exact, key=lambda c: (_days(c.date, p.date) or 0, c.row))
            take(Match("reference", "matched", [c.id], [p.id]))
            continue
        total = sum(c.amount for c in cands)
        if len(cands) > 1 and total == p.net:
            take(Match("reference_batch", "matched", [c.id for c in cands], [p.id],
                       note=f"{len(cands)} bank credits carry this reference and add up to the payout"))
            continue
        c = min(cands, key=lambda c: (abs(c.amount - p.net), c.row))
        take(Match("reference", "fuzzy", [c.id], [p.id], diff=c.amount - p.net,
                   note="reference matches but the amount differs"))

    # 2. same amount within a few days
    for p in ordered:
        if p.id not in free_p or not p.date:
            continue
        cands = [(abs((c.date - p.date).days), c.row, c) for c in amount_index.get(p.net, ())
                 if c.id in free_c and c.date and abs((c.date - p.date).days) <= DATE_WINDOW_DAYS]
        if cands:
            c = min(cands, key=lambda t: t[:2])[2]
            take(Match("amount_date", "fuzzy", [c.id], [p.id], note=f"same amount within {DATE_WINDOW_DAYS} days, no shared reference"))

    # 3. many bank credits for one payout batch, net of fees (or gross, when fees are debited separately)
    for p in ordered:
        if p.id not in free_p or not p.date:
            continue
        near = [c for c in _window(credit_dates, p.date, BATCH_WINDOW_DAYS) if c.id in free_c]
        if len(near) < 2:
            continue
        for kind, target in (("batch", p.net), ("batch_gross", p.gross)):
            if target is None or target <= 0 or (kind == "batch_gross" and target == p.net):
                continue
            sets = _subsets(near, target, key=lambda c: c.amount, budget=budget)
            if sets:
                best = min(sets, key=lambda cs: (_spread([c.date for c in cs]), _credit_mid(cs, p), len(cs)))
                note = (f"{len(best)} bank credits add up to this payout" if kind == "batch" else
                        f"{len(best)} bank credits add up to the payout before fees; fees were probably debited separately")
                take(Match(kind, "fuzzy", [c.id for c in best], [p.id], note=note))
                break

    # 3b. one bank credit for several payouts
    for c in sorted(list(free_c.values()), key=lambda c: (c.date or date.min, c.row)):
        if c.id not in free_c or not c.date:
            continue
        near = [p for p in _window(payout_dates, c.date, BATCH_WINDOW_DAYS) if p.id in free_p]
        if len(near) < 2:
            continue
        sets = _subsets(near, c.amount, key=lambda p: p.net, budget=budget)
        if sets:
            best = min(sets, key=lambda ps: (_spread([p.date for p in ps]), len(ps)))
            take(Match("payout_group", "fuzzy", [c.id], [p.id for p in best],
                       note=f"one bank credit covers {len(best)} payouts"))

    # 4. invoices explain customer payments that did not come through the gateway
    pool = {i.id: i for i in (invoices or [])}
    if pool:
        by_first: dict[str, list[Invoice]] = {}
        by_amount: dict[int, list[Invoice]] = {}
        for i in pool.values():
            toks = _tokens(i.number)
            if len(norm_ref(i.number)) >= 4 and toks:
                by_first.setdefault(toks[0], []).append(i)
                if toks[0].startswith("INV") and len(toks[0]) > 3:
                    by_first.setdefault(toks[0][3:], []).append(i)
            by_amount.setdefault(i.amount, []).append(i)
        for c in sorted(list(free_c.values()), key=lambda c: (c.date or date.min, c.row)):
            toks = _tokens(c.desc + " " + c.ref)
            padded = " " + " ".join(toks) + " "
            hit = None
            # token order, then the bare form of an INV-prefixed token; never a set, so the result does not depend on
            # PYTHONHASHSEED. Among invoices that qualify, one whose amount equals the credit wins, then the first.
            probes = list(dict.fromkeys(toks + [t[3:] for t in toks if t.startswith("INV") and len(t) > 3]))
            found: dict[str, Invoice] = {}
            for t in probes:
                for i in by_first.get(t, ()):
                    if i.id not in pool or i.id in found:
                        continue
                    if (" " + " ".join(_tokens(i.number)) + " ") in padded or norm_ref(i.number).removeprefix("INV") in {t, t.removeprefix("INV")}:
                        found[i.id] = i
            # an invoice with the same amount wins; otherwise the first found (probe order, then input order)
            hit = next((i for i in found.values() if i.amount == c.amount), next(iter(found.values()), None))
            if hit:
                pool.pop(hit.id)
                take(Match("invoice_ref", "matched" if hit.amount == c.amount else "fuzzy", [c.id], [],
                           diff=c.amount - hit.amount, note=f"invoice {hit.number}" + ("" if hit.amount == c.amount else " (amount differs)")))
                continue
            cand = [i for i in by_amount.get(c.amount, ()) if i.id in pool and (i.date is None or c.date is None or
                    -2 <= (c.date - i.date).days <= INVOICE_WINDOW_DAYS)]
            if cand:
                inv = min(cand, key=lambda i: abs((c.date - i.date).days) if c.date and i.date else 0)
                pool.pop(inv.id)
                take(Match("invoice_amount", "fuzzy", [c.id], [], note=f"same amount as invoice {inv.number}"))

    warnings = list(bank.warnings)
    if budget.left <= 0:
        warnings.append("This file is large: batch matching was cut short, so some credits that add up to a payout may show as unmatched.")
    return Recon(bank, payouts, invoices or [], matches,
                 sorted(free_c.values(), key=lambda c: (c.date or date.min, c.row)),
                 sorted(free_p.values(), key=lambda p: (p.date or date.min, p.row)),
                 currency or bank.currency, warnings)


def reconcile_best(bank: Bank, st: Settlements, invoices: list[Invoice], inv_warnings: list[str] | None = None) -> Recon:
    """Reconcile; when the settlement amount column is ambiguous (gross or net) keep the reading the bank agrees with."""
    cur = bank.currency or st.currency
    best = reconcile(bank, st.payouts, invoices, cur)
    if st.alt is not None:
        second = reconcile(bank, st.alt, invoices, cur)
        score = lambda r: (sum(c.amount for c in r.bank.credits) - sum(c.amount for c in r.unmatched_credits))  # noqa: E731
        if score(second) > score(best):
            best = second
    best.warnings = best.warnings + st.warnings + list(inv_warnings or [])
    return best


def run(bank_bytes: bytes, settle_bytes: bytes, invoice_bytes: bytes | None = None) -> Recon:
    """Parse the files and reconcile them. ValueError carries a message fit to show the user."""
    bank = load_bank(bank_bytes)
    st = load_settlements(settle_bytes)
    invoices, inv_warnings = (load_invoices(invoice_bytes) if invoice_bytes else ([], []))
    return reconcile_best(bank, st, invoices, inv_warnings)


# ------------------------------------------------------------------------------------------------- fees

@dataclass
class GatewayFees:
    gateway: str
    payouts: int
    gross: int
    fee: int
    tax: int
    net: int
    pct: float | None
    all_in_pct: float | None
    expected_pct: float
    overcharge: int
    flagged: list[str]
    basis: str  # "fee" when the file has a fee column, "gross-net" otherwise


GST_RATE = Decimal("1.18")  # fees on payments carry 18% GST; a file with only gross and net hides it inside the difference


def _half_up(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def gst_applies(currency: str) -> bool:
    """GST is an Indian tax: assume it only on INR (or an unstated currency) settlements."""
    return currency.upper() in ("", "INR")


def split_fee(p: Payout, currency: str = "") -> tuple[int, int]:
    """(fee, tax) of a payout in minor units. With a fee column the file says it; with only gross and net the
    difference is fee plus 18% GST (INR or no currency stated), so the fee is the difference over 1.18. In any other
    currency the whole difference is the fee."""
    if p.gross is None:
        return 0, 0
    if p.fee is not None:
        return p.fee, p.tax or 0
    total = p.gross - p.net
    if p.tax is not None:
        return total - p.tax, p.tax
    if not gst_applies(currency):
        return total, 0
    fee = _half_up(Decimal(total) / GST_RATE)
    return fee, total - fee


def compute_fees(payouts: list[Payout], expected_pct: float = 2.0, currency: str = "") -> list[GatewayFees]:
    """Effective fee % (excluding GST) per gateway, flagging payouts charged above the expected rate."""
    by: dict[str, list[Payout]] = {}
    for p in payouts:
        if p.gross:
            by.setdefault(p.gateway, []).append(p)
    out = []
    exp = Decimal(str(expected_pct))
    for gw, ps in sorted(by.items()):
        gross = sum(p.gross for p in ps)
        has_fee = any(p.fee is not None for p in ps)
        splits = {p.id: split_fee(p, currency) for p in ps}
        fee = sum(f for f, _ in splits.values())
        tax = sum(t for _, t in splits.values())
        net = sum(p.net for p in ps)
        flagged, over = [], 0
        for p in ps:
            pfee = splits[p.id][0]
            if pfee / p.gross * 100 > expected_pct + FEE_TOLERANCE_PCT:
                flagged.append(p.id)
                over += pfee - _half_up(Decimal(p.gross) * exp / 100)
        out.append(GatewayFees(gw, len(ps), gross, fee, tax, net, round(fee / gross * 100, 3) if gross else None,
                               round((gross - net) / gross * 100, 3) if gross else None, expected_pct, over, flagged,
                               "fee" if has_fee else "gross-net"))
    return out


def payout_fee_pct(p: Payout, currency: str = "") -> float | None:
    """The fee rate excluding GST."""
    if not p.gross:
        return None
    return round(split_fee(p, currency)[0] / p.gross * 100, 3)
