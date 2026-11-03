# Trublica examples

Small agents you can deploy on [Trublica](https://github.com/kartikpathe-kp) with one click — paste this repo's URL
(or a folder link like `.../tree/main/echo-agent`) into **Add → GitHub Repository**.

| Folder | Framework | Needs |
| --- | --- | --- |
| [echo-agent](echo-agent) | plain function | nothing |
| [anthropic-agent](anthropic-agent) | Anthropic SDK + tools | `ANTHROPIC_API_KEY` |
| [openai-agent](openai-agent) | OpenAI Agents SDK | `OPENAI_API_KEY` |
| [langgraph-agent](langgraph-agent) | LangGraph / LangChain | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| [deep-agent](deep-agent) | Deep Agents | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |

Each folder is a self-contained agentpack: `requirements.txt` + an `agent.py` exporting `agent` (or `graph`). No server code.
Add API keys under the service's **Variables** tab, redeploy, then call `POST <service URL>/runs`.
