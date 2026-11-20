#!/usr/bin/env bash
# Branch otari: start the self-hosted Otari gateway (:8000) and the app (:${APP_PORT:-8781}), detached.
# Needs NEBIUS_API_KEY, TAVILY_API_KEY, OTARI_API_KEY and OTARI_MASTER_KEY in the environment
# (never in a file in the repo). PIDs: .otari-gw.pid, .otari-gw-app.pid. Stop: kill -- -$(cat .otari-gw.pid) etc.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=${APP_PORT:-8781}
uv run python scripts/otari_config.py .otari-gw/config-v2.yml --app-url "http://127.0.0.1:$PORT"
(cd .otari-gw && setsid nohup .venv/bin/otari serve --config config-v2.yml > ../.otari-gw.log 2>&1 < /dev/null &)
sleep 1; pgrep -nf 'otari serve --config config-v2.yml' > .otari-gw.pid
for _ in $(seq 40); do curl -s -o /dev/null http://127.0.0.1:8000/api/v1/models && break; sleep 1; done
# the builtin fetcher cannot be declared in config.yml, so its price (0: it calls no paid API) is set here
curl -s -o /dev/null -w 'builtin_fetch price: HTTP %{http_code}\n' -X POST http://127.0.0.1:8000/api/v1/pricing \
  -H "Authorization: Bearer $OTARI_MASTER_KEY" -H 'Content-Type: application/json' \
  -d '{"kind":"fetch","model_key":"builtin_fetch","input_price_per_million":0,"output_price_per_million":0}'
LLM_GATEWAY=otari setsid nohup uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
  > .otari-app.log 2>&1 < /dev/null & echo $! > .otari-gw-app.pid
for _ in $(seq 20); do curl -s -o /dev/null "http://127.0.0.1:$PORT/healthz" && break; sleep 1; done
echo "gateway pid $(cat .otari-gw.pid), app pid $(cat .otari-gw-app.pid), app http://127.0.0.1:$PORT/live"
