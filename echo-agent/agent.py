"""The smallest possible agent: no framework, no API key. Any function that takes a payload and returns JSON works."""
import datetime


async def agent(payload):
    message = payload.get("message", "") if isinstance(payload, dict) else str(payload)
    return {
        "reply": f"echo: {message}",
        "words": len(message.split()),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
