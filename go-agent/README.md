# go-agent

Not Python, not Node: a Go service with its own `Dockerfile`. If a repo has a Dockerfile and no `requirements.txt`/`package.json`
(or `agent.yaml` says `runtime: docker`), Trublica builds it as-is. Contract: listen on `$PORT` (8080), answer `GET /healthz`.
