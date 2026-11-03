# openai-agent

OpenAI Agents SDK. Needs `OPENAI_API_KEY`; set `MODEL` to change the model. The body's `input` is the prompt.

```bash
curl -X POST $URL/runs -H 'content-type: application/json' -d '{"input":"What time is it in Tokyo?"}'
curl -N -X POST $URL/runs/stream -H 'content-type: application/json' -d '{"input":"Say hi"}'
```
