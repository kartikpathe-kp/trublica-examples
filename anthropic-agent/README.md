# anthropic-agent

Claude + tools using the plain Anthropic SDK (manual tool-use loop). Needs `ANTHROPIC_API_KEY`; set `MODEL` to change the model.

```bash
curl -X POST $URL/runs -H 'content-type: application/json' -d '{"message":"What is (12+8)*3/4, and what time is it?"}'
```
