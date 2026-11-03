# langgraph-agent

LangChain/LangGraph `create_agent`. Needs `ANTHROPIC_API_KEY` (default) — or set `MODEL=openai:gpt-4.1-mini` and `OPENAI_API_KEY`.

```bash
curl -X POST $URL/runs -H 'content-type: application/json' \
  -d '{"input":{"messages":[{"role":"user","content":"Reverse the word trublica"}]}}'
```
