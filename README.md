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
| [node-agent](node-agent) | TypeScript (streaming) | nothing |
| [anthropic-node-agent](anthropic-node-agent) | TypeScript + Anthropic SDK | `ANTHROPIC_API_KEY` |
| [go-agent](go-agent) | Go, via its own Dockerfile | nothing |

Python folders are agentpacks: `requirements.txt` + an `agent.py` exporting `agent` (or `graph`). TypeScript folders: `package.json` + an `agent.ts` exporting `agent`. Anything else: a `Dockerfile`. No server code needed except in the Dockerfile case.
Add API keys under the service's **Variables** tab, redeploy, then call `POST <service URL>/runs`.
