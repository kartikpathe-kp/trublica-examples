"""The agent's three platform calls: read an input file, ask the model, write the output file.

Each reads the run's gateway base and token from the shim context (set from the request headers), never from the
environment, and never uses a proxy. Tests replace these functions.
"""

from __future__ import annotations

import os

import httpx

MODEL = os.environ.get("MODEL") or "anthropic/claude-sonnet-5.5"
MAX_TOKENS = 700
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _gateway():
    from trublica_shim.context import gateway

    g = gateway()
    if g is None:
        raise RuntimeError("this agent runs inside a Trublica run")
    return g


def _client() -> httpx.Client:
    return httpx.Client(timeout=120, trust_env=False)


def fetch(ref) -> bytes:
    """The bytes of an uploaded file: `ref` is the descriptor the run received."""
    g = _gateway()
    url = ref.get("url") if isinstance(ref, dict) else None
    art = ref.get("artifact") if isinstance(ref, dict) else ref
    with _client() as c:
        r = c.get(url or f"{g.base}/artifacts/{art}", headers={"Authorization": f"Bearer {g.token}"})
    if r.status_code >= 400:
        raise RuntimeError(f"could not read an uploaded file ({r.status_code})")
    return r.content


def ask_model(prompt: str) -> str:
    g = _gateway()
    body = {"model": MODEL, "max_tokens": MAX_TOKENS, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    with _client() as c:
        r = c.post(f"{g.openrouter_base}/chat/completions", json=body, headers={"Authorization": f"Bearer {g.token}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def put_output(name: str, data: bytes, mime: str = XLSX) -> dict:
    g = _gateway()
    with _client() as c:
        r = c.put(f"{g.base}/artifacts/{name}", content=data, headers={"Authorization": f"Bearer {g.token}", "Content-Type": mime})
    if r.status_code >= 400:
        raise RuntimeError(f"could not save {name} ({r.status_code})")
    return r.json()
