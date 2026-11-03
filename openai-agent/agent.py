"""An OpenAI Agents SDK agent with two function tools. Trublica detects the `Agent` object and serves it
over /runs (sync) and /runs/stream (SSE, token deltas)."""
import datetime
import os
from zoneinfo import ZoneInfo

from agents import Agent, function_tool


@function_tool
def current_time(timezone: str = "UTC") -> str:
    """Current date and time in an IANA timezone, e.g. 'Asia/Kolkata'."""
    return datetime.datetime.now(ZoneInfo(timezone)).isoformat()


@function_tool
def word_count(text: str) -> int:
    """Count the words in some text."""
    return len(text.split())


agent = Agent(
    name="Assistant",
    instructions="You are a concise assistant. Use tools when they help.",
    tools=[current_time, word_count],
    model=os.environ.get("MODEL", "gpt-4.1-mini"),
)
