# deep-agent

LangChain Deep Agents. Needs `ANTHROPIC_API_KEY` (default) — or `MODEL=openai:gpt-4.1-mini` + `OPENAI_API_KEY`.

```bash
curl -X POST $URL/runs -H 'content-type: application/json' \
  -d '{"input":{"messages":[{"role":"user","content":"Plan a 3-step approach to learn Rust, then summarise it."}]}}'
```
