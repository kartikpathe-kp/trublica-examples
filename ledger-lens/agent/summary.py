"""The plain-language summary: the model writes it from aggregates; a template covers any failure.

The model never sees a row, a description or a reference: only counts, totals and percentages.
"""

from __future__ import annotations

import json
import re

MARKER = "LedgerLens summary request"
MAX_LINES = 6
MAX_LEN = 400


def money(minor: int, cur: str = "") -> str:
    sign = "-" if minor < 0 else ""
    major, cents = divmod(abs(minor), 100)
    s = f"{major:,}"
    if cents:
        s += f".{cents:02d}"
    return f"{sign}{cur + ' ' if cur else ''}{s}"


def template(agg: dict) -> dict:
    """A deterministic summary of the aggregates."""
    cur = agg.get("currency", "")
    credits, un = agg["bank_credits"], agg["unmatched"]
    explained = agg["matched"]["count"] + agg["fuzzy"]["count"]
    headline = f"{explained} of {credits['count']} bank credits are explained by your settlements."
    findings = []
    if agg["fuzzy"]["count"]:
        findings.append(f"{agg['fuzzy']['count']} credits ({money(agg['fuzzy']['total'], cur)}) matched only on amount, date or after fees; "
                        "they are amber in the workbook, worth a quick look.")
    if un["count"]:
        findings.append(f"{un['count']} credits worth {money(un['total'], cur)} match no settlement.")
    for name, v in agg.get("labels", {}).items():
        if name != "unlabelled":
            findings.append(f"You marked {v['count']} unmatched credits ({money(v['total'], cur)}) as {name}.")
    if agg["missing_payouts"]["count"]:
        findings.append(f"{agg['missing_payouts']['count']} settlements ({money(agg['missing_payouts']['total'], cur)}) never reached the bank.")
    flagged = [f for f in agg["fees"] if f["flagged_payouts"]]
    for f in agg["fees"][:4]:
        findings.append(f"{f['gateway']}: effective fee {f['pct']:.2f}% over {f['payouts']} payouts"
                        + (f", above the expected {f['expected_pct']:.2f}% on {f['flagged_payouts']} of them." if f["flagged_payouts"] else "."))
    if not findings:
        findings.append("Everything reconciles.")
    steps = []
    if un["count"]:
        steps.append("Review the red rows on the Unmatched sheet.")
    if flagged:
        steps.append("Ask the gateway about the payouts charged above the expected fee.")
    if agg["missing_payouts"]["count"]:
        steps.append("Check the bank for the settlements that never arrived.")
    return {"headline": headline, "findings": findings[:MAX_LINES], "next_steps": steps[:MAX_LINES]}


def prompt(agg: dict) -> str:
    return (f"{MARKER}. You write the summary of a bank and payment-gateway reconciliation for a founder. "
            "Use only the numbers below (amounts are in minor units, 100 = 1.00). Do not invent figures. "
            'Reply with a single JSON object: {"headline": string, "findings": [string], "next_steps": [string]}. '
            f"At most {MAX_LINES} findings and {MAX_LINES} next steps, each one plain sentence.\n\n" + json.dumps(agg, sort_keys=True))


def _strings(v, limit: int) -> list[str] | None:
    if not isinstance(v, list):
        return None
    out = [x.strip()[:MAX_LEN] for x in v if isinstance(x, str) and x.strip()]
    return out[:limit]


def parse_reply(text: str) -> dict | None:
    """The summary dict from a model reply, tolerating code fences and prose around the JSON; None if unusable."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S | re.I)
    if fence:
        t = fence.group(1).strip()
    obj = None
    try:
        obj = json.loads(t)
    except ValueError:
        start = t.find("{")
        while start != -1 and obj is None:
            try:
                obj, _ = json.JSONDecoder().raw_decode(t[start:])
            except ValueError:
                start = t.find("{", start + 1)
    if not isinstance(obj, dict):
        return None
    headline = obj.get("headline")
    findings = _strings(obj.get("findings"), MAX_LINES)
    steps = _strings(obj.get("next_steps", []), MAX_LINES)
    if not isinstance(headline, str) or not headline.strip() or not findings or steps is None:
        return None
    return {"headline": headline.strip()[:MAX_LEN], "findings": findings, "next_steps": steps}


def unmask(text: dict, names: dict[str, str]) -> dict:
    """Put the real gateway names back into a summary written about "Gateway 1".."Gateway 10"."""
    def fix(s: str) -> str:
        return re.sub(r"Gateway \d+", lambda m: names.get(m.group(0), m.group(0)), s)
    return {"headline": fix(text["headline"]), "findings": [fix(x) for x in text["findings"]],
            "next_steps": [fix(x) for x in text.get("next_steps", [])]}


def explain(agg: dict, ask) -> tuple[dict, bool]:
    """(summary, from_model). `ask(prompt)` returns the model text; any failure or unusable reply gives the template."""
    try:
        parsed = parse_reply(ask(prompt(agg)))
    except Exception:  # noqa: BLE001 - the summary is a nicety; the reconciliation stands without it
        parsed = None
    return (parsed, True) if parsed else (template(agg), False)
