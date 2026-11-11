"""Reading messy bank and gateway exports: encodings, delimiters, header rows, dates, amounts and references.

Pure functions over bytes and strings. Error messages are written for the person who uploaded the file and never
echo cell contents.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200_000
HEADER_SCAN_ROWS = 40


# --------------------------------------------------------------------------------------------------- decoding

def decode_text(data: bytes, what: str) -> str:
    """Bytes of an uploaded text file to a string, or a ValueError saying what is wrong with it."""
    if not data or not data.strip():
        raise ValueError(f"The {what} file is empty. Export it again and attach the CSV.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"The {what} file is larger than {MAX_BYTES // (1024 * 1024)} MB. Export a shorter period.")
    if data[:2] == b"PK":
        raise ValueError(f"The {what} file is an Excel or zip file, not a CSV. Save it as CSV (comma separated) and attach that.")
    if data[:4] == b"%PDF":
        raise ValueError(f"The {what} file is a PDF, not a CSV. Download the statement as CSV from your bank or gateway.")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    else:
        if b"\x00" not in data[:8192]:
            for enc in ("utf-8-sig", "cp1252"):  # cp1252: older Windows exports with a stray accent or a Rs. sign
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    continue
    raise ValueError(f"The {what} file is not readable text (it is not UTF-8). Export it as a CSV with UTF-8 encoding.")


def _sniff_delimiter(lines: list[str]) -> str:
    sample = [ln for ln in lines if ln.strip()][:30]
    best, best_score = ",", -1
    for d in (",", ";", "\t", "|"):
        counts = [ln.count(d) for ln in sample]
        if not counts or max(counts) == 0:
            continue
        # the delimiter that appears the same number of times on most lines wins
        mode = max(set(counts), key=counts.count)
        score = mode * counts.count(mode) if mode else 0
        if score > best_score:
            best, best_score = d, score
    return best


def read_rows(data: bytes, what: str) -> list[list[str]]:
    """All non-blank rows of a CSV as lists of stripped strings."""
    text = decode_text(data, what)
    lines = text.splitlines()
    delim = _sniff_delimiter(lines)
    try:
        rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]
    except csv.Error:
        raise ValueError(f"The {what} file could not be read as a CSV. Check that it is a plain comma separated export.") from None
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise ValueError(f"The {what} file has no rows.")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"The {what} file has more than {MAX_ROWS:,} rows. Export a shorter period.")
    return rows


def norm_header(h: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", h.lower()).split())


# --------------------------------------------------------------------------------------------------- amounts

_CURRENCY = (("₹", "INR"), ("rs.", "INR"), ("rs ", "INR"), ("inr", "INR"), ("usd", "USD"), ("$", "USD"),
             ("eur", "EUR"), ("€", "EUR"), ("gbp", "GBP"), ("£", "GBP"), ("aed", "AED"), ("sgd", "SGD"))
_JUNK = re.compile(r"(₹|rs\.?|inr|usd|eur|gbp|aed|sgd|[$€£]|/-|\s| |')", re.I)
_MARK = re.compile(r"(?:^|\b)(cr|dr|credit|debit)\.?\s*$|^\s*(cr|dr|credit|debit)\b\.?", re.I)
_BLANKS = {"", "-", "--", "—", "na", "n/a", "nil", "null", "none", "."}


def currency_of(text: str) -> str:
    low = text.lower()
    for sym, code in _CURRENCY:
        if sym in low:
            return code
    return ""


def parse_amount(text: str) -> tuple[int | None, str]:
    """(minor units, marker) of an amount cell. None for a blank cell; ValueError for something that is not a number.

    Handles Indian grouping (1,23,456.78), a leading rupee sign or Rs., INR, brackets and trailing minus as negatives,
    and a Dr or Cr marker (returned as "dr" or "cr"; it does not change the sign here).
    """
    s = (text or "").strip()
    if s.lower() in _BLANKS:
        return None, ""
    marker = ""
    m = _MARK.search(s)
    if m:
        marker = "cr" if (m.group(1) or m.group(2)).lower().startswith("c") else "dr"
        s = _MARK.sub("", s, count=1)
    neg = False
    s = s.replace("−", "-").replace("–", "-")
    s = _JUNK.sub("", s)
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-"):
        neg, s = True, s[:-1]
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not s or not re.fullmatch(r"[0-9.,]+", s):
        raise ValueError("not a number")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        group = "." if dec == "," else ","
        s = s.replace(group, "").replace(dec, ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2 and len(parts[0]) <= 3:
            s = parts[0] + "." + parts[1]  # 12,5 and 3,50 are decimal commas
        else:
            s = s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError("not a number") from None
    minor = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return (-minor if neg else minor), marker


def fmt_money(minor: int, currency: str = "", signed: bool = False) -> str:
    """42500_00 -> '₹42,500.00' (Indian grouping for INR, thousands separators otherwise)."""
    sign = "-" if minor < 0 else ("+" if signed and minor > 0 else "")
    whole, frac = divmod(abs(minor), 100)
    digits = str(whole)
    if currency == "INR" and len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    else:
        digits = f"{whole:,}"
    symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    code = "" if symbol or not currency else currency + " "
    return f"{sign}{symbol}{code}{digits}.{frac:02d}"


# --------------------------------------------------------------------------------------------------- dates

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_NUMERIC = re.compile(r"^(\d{1,4})[-/. ](\d{1,2})[-/. ](\d{2,4})(?![\d])")
_NAMED = re.compile(r"^(\d{1,2})[-/. ,]*([A-Za-z]{3,9})\.?[-/. ,]*(\d{2,4})(?![\d])")
_NAMED_US = re.compile(r"^([A-Za-z]{3,9})\.?[ -/]*(\d{1,2})(?:st|nd|rd|th)?,?[ -/]*(\d{4})(?![\d])")


def _year(y: int) -> int:
    return y if y >= 100 else (2000 + y if y < 70 else 1900 + y)


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: str, order: str = "dmy") -> date | None:
    """A date cell to a date, or None. `order` ("dmy" or "mdy") resolves 05/10/2024-style dates; the Indian default is
    day first. Understands 2024-10-05, 05/10/2024, 05-Oct-2024, Oct 5, 2024 and any of them followed by a time."""
    s = (text or "").strip()
    if not s:
        return None
    s = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", "", s, flags=re.I)
    m = _NUMERIC.match(s)
    if m:
        a, b, c = (int(x) for x in m.groups())
        if len(m.group(1)) == 4:
            return _mk(a, b, c)
        y = _year(c)
        return _mk(y, b, a) if order == "dmy" else _mk(y, a, b)
    m = _NAMED.match(s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _mk(_year(int(m.group(3))), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    m = _NAMED_US.match(s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _mk(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    return None


def detect_date_order(values: list[str]) -> str:
    """"mdy" only when the column proves it (a value like 10/25/2024); day first otherwise."""
    first_big = second_big = 0
    for v in values:
        m = _NUMERIC.match((v or "").strip())
        if not m or len(m.group(1)) == 4:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        first_big += a > 12
        second_big += b > 12
    return "mdy" if second_big and not first_big else "dmy"


# --------------------------------------------------------------------------------------------------- references

_REF_PATTERNS = [
    re.compile(r"\b[A-Z]{4}[NRE]\d{9,20}\b"),                       # NEFT / RTGS UTR: HDFCN52024100512345
    re.compile(r"\b[A-Z]{4}\d{12,22}\b"),                           # bank prefix + digits
    re.compile(r"\b(?:POUT|SETL|PAY|PO|PAYOUT|SETTLEMENT|ORDER|TXN|CF|BATCH)[_-][A-Z0-9]{6,}\b"),   # gateway ids
    re.compile(r"\b\d{11,22}\b"),                                   # UPI / IMPS RRN, RTGS digits
    re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,}\b"),  # any long mixed token
]
_SEPARATED = re.compile(r"(?:UPI|IMPS|NEFT|RTGS|ACH|NACH|UTR|REF)[/\-: ]+(?:[A-Z]+[/\-: ]+)?([A-Z0-9]{8,})")


def norm_ref(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _plausible(token: str) -> bool:
    return len(token) >= 8 and len(set(token)) > 2 and not re.fullmatch(r"0+\d{0,3}", token)


def extract_refs(*texts: str) -> frozenset[str]:
    """Candidate references found in descriptions and reference cells, normalized (upper case, letters and digits)."""
    out: set[str] = set()
    for text in texts:
        if not text:
            continue
        up = text.upper()
        # gateway ids use underscores (setl_Abc123): keep them joined
        for pat in _REF_PATTERNS:
            out.update(norm_ref(m.group(0)) for m in pat.finditer(up))
        out.update(norm_ref(m.group(1)) for m in _SEPARATED.finditer(up))
        whole = norm_ref(up)
        if len(up) <= 40 and _plausible(whole):  # a short cell that is itself a reference
            out.add(whole)
    return frozenset(t for t in out if _plausible(t))


def refs_overlap(a: frozenset[str], b: frozenset[str]) -> bool:
    """Equal tokens, or one UTR carrying a bank prefix the other lacks (HDFCN5202... vs N5202...)."""
    if a & b:
        return True
    for x in a:
        if len(x) < 12:
            continue
        for y in b:
            if len(y) >= 12 and (x.endswith(y) or y.endswith(x)):
                return True
    return False
