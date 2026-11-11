# LedgerLens

A confidential reconciler for founders and finance teams. Give it a bank statement and a payment-gateway settlements
report (and optionally invoices); it matches every bank credit to a settlement, works out what each gateway really
charged, asks you about the credits it cannot explain, and hands back `reconciliation.xlsx`.

- Inputs: `bank` (CSV), `settlements` (CSV), optional `invoices` (CSV, or a ZIP of PDFs), optional `expected_fee_pct` (default 2.0).
- Reads Indian bank exports as they come: separate debit and credit columns or one Dr/Cr amount, dd/mm/yyyy dates,
  `1,23,456.78` and rupee-sign amounts, UPI, NEFT and UTR references inside the narration. Any currency works.
- Matching: exact reference, then the same amount within 3 days, then several bank credits that add up to one payout
  batch (net of fees), then one credit for several payouts, then invoices.
- Unmatched credits pause the run and list them; reply with a label per credit (refund, capital, other) or skip.
- The workbook has Summary, Matched, Unmatched and Fees sheets. Green is matched, amber a fuzzy match to check, red unmatched.
- Confidential: the container has no network (`network.allow` is empty), the model sees only totals, counts and
  percentages, and nothing logs file contents.

## How it is wired

- `manifest.json` is the publish body. File inputs need `run_type: "form"`; the chat look is the `surface`.
- `agent/agent.yaml` says `entry: agent:graph`: the Trublica shim imports `agent.py` and serves its LangGraph graph
  (nodes `load`, `match`, `fees`, `clarify`, `explain`, `export`). The platform builds the image, so there is no Dockerfile.
- The model id comes from `MODEL` (default `anthropic/claude-sonnet-5.5`) and every call goes through the run's gateway.

## Tests

```
cd ledger-lens/agent
uv run --with-requirements requirements-dev.txt pytest -q
```

The end-to-end test is `api/tests/test_e2e_agent_ledger_lens.py` in the platform repo (`TRUBLICA_E2E=1`).
