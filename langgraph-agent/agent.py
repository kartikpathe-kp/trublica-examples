"""A LangGraph tool-calling agent. Expose the compiled graph as `graph`; Trublica streams node updates over SSE."""
import datetime
import os

from langchain.agents import create_agent
from langchain_core.tools import tool


@tool
def current_time() -> str:
    """Current UTC date and time."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@tool
def reverse(text: str) -> str:
    """Reverse a string."""
    return text[::-1]


graph = create_agent(
    model=os.environ.get("MODEL", "anthropic:claude-sonnet-5-5"),
    tools=[current_time, reverse],
    system_prompt="You are a concise assistant. Use tools when they help.",
)
