# Trublica examples

Example agents you can publish on [Trublica](https://trublica.milo.study). Each folder has a `manifest.json` (the body
you post to `/v1/projects/{project}/listing/publish`), a README and its service source.

| Example | Surface | What it shows |
|---|---|---|
| [`ledger-lens`](ledger-lens/) | form, one sealed service | Confidential bank vs gateway settlement reconciliation: files in, `.xlsx` out, a question when credits stay unmatched, no network egress |

## Tests

Unit tests run offline:

```
cd ledger-lens/agent
uv run --with-requirements requirements-dev.txt pytest -q
```

The Docker end-to-end test lives in the platform repo (`api/tests/test_e2e_agent_ledger_lens.py`). It reads this
checkout from `TRUBLICA_EXAMPLES_DIR`, or from a sibling `../trublica-examples` folder by default.
