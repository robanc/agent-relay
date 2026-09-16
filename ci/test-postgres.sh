#!/usr/bin/env bash
set -euo pipefail
name="agent-relay-ci-$(cat /proc/sys/kernel/random/uuid)"
api_pid=""
cleanup() {
  if [[ -n "$api_pid" ]]; then kill "$api_pid" 2>/dev/null || true; wait "$api_pid" 2>/dev/null || true; fi
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# No persistent volume and no access to Kubernetes PostgreSQL.
# Trust authentication is limited to this short-lived, isolated local CI DB.
if [[ "${ACT:-}" == true ]]; then
  docker run -d --name "$name" --network kind --tmpfs /var/lib/postgresql/data -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=relay -e POSTGRES_DB=relay postgres:16-alpine >/dev/null
  dbhost="$name"
  dbport=5432
else
  docker run -d --name "$name" -p 127.0.0.1::5432 --tmpfs /var/lib/postgresql/data -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=relay -e POSTGRES_DB=relay postgres:16-alpine >/dev/null
  dbhost=127.0.0.1
  dbport=$(docker port "$name" 5432/tcp | cut -d: -f2)
fi
for i in {1..60}; do
  if docker exec "$name" pg_isready -U relay -d relay >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$name" pg_isready -U relay -d relay
export RELAY_DATABASE_URL="postgresql+psycopg://relay@${dbhost}:${dbport}/relay"
uv run --locked uvicorn main:app --host 127.0.0.1 --port 18000 >/tmp/agent-relay-ci-api.log 2>&1 &
api_pid=$!
uv run --locked python ci/wait-ready.py
uv run --locked python verify_container.py --base-url http://127.0.0.1:18000
uv run --locked python verify_postgres.py
