"""Claude with tools, via the Anthropic SDK: a manual tool-use loop in ~50 lines."""
import ast
import datetime
import operator
import os

import anthropic

MODEL = os.environ.get("MODEL", "claude-sonnet-5-5")
SYSTEM = "You are a concise assistant. Use the tools for arithmetic and for the current time instead of guessing."

TOOLS = [
    {
        "name": "calculate",
        "description": "Evaluate an arithmetic expression such as '(12 + 8) * 3 / 4'.",
        "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
    },
    {
        "name": "current_time",
        "description": "Current UTC date and time in ISO 8601.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only arithmetic is allowed")


def run_tool(name, args):
    if name == "calculate":
        return str(_eval(ast.parse(args["expression"], mode="eval").body))
    if name == "current_time":
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
    raise ValueError(f"unknown tool {name}")


async def agent(payload):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set — add it under Variables and redeploy")
    client = anthropic.AsyncAnthropic()
    messages = [{"role": "user", "content": payload.get("message", "")}]
    calls = []
    for _ in range(6):
        resp = await client.messages.create(model=MODEL, max_tokens=1024, system=SYSTEM, tools=TOOLS, messages=messages)
        if resp.stop_reason != "tool_use":
            return {"reply": "".join(b.text for b in resp.content if b.type == "text"), "tool_calls": calls}
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                try:
                    out, err = run_tool(block.name, block.input), False
                except Exception as e:  # noqa: BLE001 — report the failure to the model
                    out, err = str(e), True
                calls.append({"tool": block.name, "input": block.input, "output": out})
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": err})
        messages.append({"role": "user", "content": results})
    return {"reply": "Stopped after 6 tool rounds.", "tool_calls": calls}
