# node-agent

TypeScript, no framework, no key. `agent.ts` exports an async generator; Trublica runs it under `tsx` (no build step) and serves
`/runs` and `/runs/stream`.

```bash
curl -N -X POST $URL/runs/stream -H 'content-type: application/json' -d '{"message":"hello from node"}'
```
