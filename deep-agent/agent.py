"""A Deep Agent (LangChain `deepagents`): planning, a virtual filesystem and sub-agents built in. It is a
LangGraph graph, so Trublica streams every step over SSE."""
import datetime
import os

from deepagents import create_deep_agent


def current_time() -> str:
    """Current UTC date and time."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


agent = create_deep_agent(
    model=os.environ.get("MODEL", "anthropic:claude-sonnet-5-5"),
    tools=[current_time],
    system_prompt=(
        "You are a research assistant. For anything non-trivial, write a short plan with write_todos, "
        "keep notes in files, and finish with a clear, concise answer."
    ),
)
