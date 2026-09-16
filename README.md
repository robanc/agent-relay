# Agent Relay (SQLite starter)

Agent Relay is a small FastAPI service for registering agents, delivering one
task at a time, and recording results. The local starter is self-contained:
SQLite persists the queue and attempts, while workers execute tasks on their own
machines. The included worker deterministically returns `input.upper()`.

## Run it

```bash
uv sync
uv run uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/> for the token-based local dashboard. The default
database is `./agent-relay.db`; set `RELAY_DATABASE_URL` to use another SQLite
file. `GET /health` is a liveness check and `GET /ready` verifies database
connectivity and schema (it queries the real tables, so a wiped volume
reports not-ready instead of passing with zero tables).

Register two identities and send a task:

```bash
alice=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"alice"}')
bob=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"uppercase"}')
```

The response contains each agent's secret `token` once. Keep it outside source
control. Use `Authorization: Bearer <token>` for all subsequent API calls;
registration is the only unauthenticated endpoint. For a shared installation,
set `RELAY_ENROLLMENT_SECRET` and send it as `X-Enrollment-Secret` when
registering.

## Run the deterministic worker

The worker can register itself and save credentials in a mode-0600 JSON file:

```bash
uv run python main.py worker \
  --base-url http://127.0.0.1:8000 \
  --name uppercase \
  --credentials ./uppercase-credentials.json \
  --worker-id laptop-1
```

For failure/redelivery demonstrations, make local execution intentionally slow
and stop the process after one completion:

```bash
uv run python main.py worker --credentials ./uppercase-credentials.json \
  --slow-seconds 75 --worker-id slow-laptop
```

The worker heartbeats during long work. Killing it leaves the claim leased;
after the 60-second lease expires, another worker can claim the task with a new
token and incremented attempt number. `RELAY_LEASE_SECONDS` and
`RELAY_MAX_ATTEMPTS` are configurable server settings.

An existing credential can also be supplied explicitly (the token is not
written to disk):

```bash
uv run python main.py worker --agent-id agent_123 --token agt_… --worker-id laptop-2
```

## Storage and delivery behavior

`database.py` contains SQLAlchemy models, SQLite WAL setup, and the isolated
`BEGIN IMMEDIATE` transaction helper. `storage.py` contains task/claim/recovery
operations; routes and request models are kept in `main.py` and `schemas.py`.
SQLite does not provide PostgreSQL's `FOR UPDATE SKIP LOCKED`, so the starter
serializes writer transactions to make concurrent claims safe across processes.
PostgreSQL uses ordinary transactions with task row locks. Claims and recovery
use `FOR UPDATE SKIP LOCKED`; heartbeat and terminal submissions lock the task
before inspecting its attempt. Sender row locks serialize idempotent creation.
The HTTP protocol and lifecycle in `SPEC.md` are unchanged.

Claims are at-least-once and leased for 60 seconds by default. Heartbeats extend
an active lease. A completion or failure must include the recipient's bearer
token and claim token. Repeating the exact terminal request with that claim
token is idempotent; a stale token or different result receives `409`.

## Verify

The test suite covers the main protocol, sender/recipient access boundaries,
hashed claim-token behavior, idempotent terminal retries, concurrent claims,
lease expiry before and after recovery, pagination/error shape, and dashboard
asset serving:

```bash
uv run pytest -q
```

Tests configure a unique temporary SQLite database before importing the app,
regardless of database environment settings. Only that temporary database is
reset and removed; the local `agent-relay.db` is not used.

## Run with Docker

```bash
docker build -t agent-relay:local .
docker run -d --name agent-relay-q3 -p 8000:8000 --mount type=volume,source=agent-relay-q3-data,target=/data -e RELAY_DATABASE_URL=sqlite:////data/agent-relay.db agent-relay:local
```

Host port 8000 must be free. Docker creates the named volume on first use;
use a new volume name for a fresh database. This does not mount the host's
`agent-relay.db`. The database and SQLite sidecars persist in `/data` after
container removal. New volumes inherit the image directory's non-root ownership.

The multi-stage image installs locked runtime dependencies with uv, runs as
UID/GID 10001, and starts Uvicorn on `0.0.0.0:8000` without reload. A readiness
health check queries the actual schema. The build context uses an allowlist to
exclude local databases, credentials, virtual environments, and Git metadata.
This image runs the relay API; workers execute separately on their own machines.

Run the host HTTP verification against the running container:

```bash
python verify_container.py
docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' agent-relay-q3
```

The script checks the dashboard HTML/assets, health/readiness, SPEC acceptance
scenario 1, and the dashboard's authenticated API requests. It creates test
identities and keeps their tokens only in memory. It does not use TestClient.

For a visual check, open <http://127.0.0.1:8000/>, use a token registered on
this container's API, and click **Use token**. Confirm the Agents and My tasks
tables load, **Refresh** works, and a completed task displays its output and
delivery history. Host-database credentials do not apply to this separate DB.

To stop and remove the container while retaining the image and database:

```bash
docker stop agent-relay-q3
docker rm agent-relay-q3
```

## Run with Compose and PostgreSQL

If the Q3 container is still using port 8000, stop it with
`docker stop agent-relay-q3`. Keep its image and SQLite volume.

```bash
docker compose up --build
```

Services are `app` and `postgres` (official `postgres:16-alpine`). Compose builds
`agent-relay:compose`, preserving `agent-relay:local`. The app waits for
PostgreSQL's healthcheck and automatically creates the schema on startup.
Open <http://127.0.0.1:8000/>. `/health` checks liveness; `/ready` queries all
three application tables. The image's healthcheck uses `/ready`.

The app connects to `postgresql+psycopg://relay:<password>@postgres:5432/relay`.
Compose supplies disposable local defaults through `POSTGRES_USER`,
`POSTGRES_PASSWORD`, and `POSTGRES_DB`; override them through your environment
before first startup if needed. Use URL-safe values (URL-encode special password
characters in a custom connection URL). These defaults are not for production.
The database port is internal to Compose. PostgreSQL data persists in the
project's `postgres_data` named volume, separate from both SQLite databases.
Changing credentials later does not update an already initialized database.

Verify from the host, then run the isolated PostgreSQL storage checks:

```bash
python verify_container.py
docker compose exec -T app python - < verify_postgres.py
uv run pytest -v
docker compose ps
```

PowerShell equivalent for the PostgreSQL check:

```powershell
Get-Content -Raw verify_postgres.py | docker compose exec -T app python -
```

The HTTP script covers acceptance scenario 1 and dashboard API requests. The
PostgreSQL script uses a unique temporary schema and removes only that schema;
it tests locking, concurrent claims/idempotency/completion, heartbeat, expired
claims, and recovery. It does not reset the application's tables. For a visual
dashboard check, use a token registered against Compose, click **Use token**,
then **Refresh**, and verify the task status, output, and delivery history.

Stop with `docker compose stop`, or remove containers/network with
`docker compose down`. Both retain PostgreSQL data; omit `--volumes`.
To run in the background, use `docker compose up -d`.

Schema creation supports fresh databases; existing-schema migrations and
simultaneous first-start schema creation by multiple app replicas are outside
this single-app Compose setup. No cloud deployment is included.

## Local Kubernetes with kind (Windows/PowerShell)

Prerequisites: Docker Desktop running Linux containers, kind, and kubectl.
If missing, install kind with `winget install --id Kubernetes.kind --exact`;
Docker Desktop supplies kubectl. Verify `kind version` and
`kubectl version --client` (open a new terminal after installing).

```powershell
kind get clusters
# Create only if agent-relay does not already exist:
kind create cluster --name agent-relay --wait 120s
kubectl cluster-info --context kind-agent-relay
kubectl --context kind-agent-relay wait --for=condition=Ready nodes --all --timeout=120s
docker build -t agent-relay:local .
kind load docker-image agent-relay:local --name agent-relay
powershell -ExecutionPolicy Bypass -File k8s/create-secret.ps1
kubectl --context kind-agent-relay apply -f k8s/
kubectl --context kind-agent-relay rollout status deployment/postgres --timeout=120s
kubectl --context kind-agent-relay rollout status deployment/agent-relay --timeout=120s
kubectl --context kind-agent-relay get nodes,pods,deployments,services,pvc
```

The Secret helper generates a URL-safe random local password directly into
Kubernetes and preserves an existing Secret. No password is saved in the repo.
The app uses `postgres` service DNS, waits for PostgreSQL in an init container,
and initializes its schema automatically. Probes check `/ready` and `/health`.
`imagePullPolicy: Never` requires the local image to be loaded into kind.
After rebuilding/reloading an existing deployment, restart it with
`kubectl --context kind-agent-relay rollout restart deployment/agent-relay`.

PostgreSQL uses `postgres:16-alpine` and a 1Gi PVC provisioned by kind's default
storage class. A single replica and Recreate strategy avoid simultaneous
PostgreSQL processes on the same data directory. Data survives pod replacement,
but kind node/cluster deletion removes this local storage. SQLite and Compose
volumes are separate and remain untouched.

If Compose occupies port 8000, stop it without removing its data:

```powershell
docker compose stop
kubectl --context kind-agent-relay port-forward service/agent-relay 8000:8000
```

Keep port-forward running. Open <http://127.0.0.1:8000/>. In another terminal:

```powershell
python verify_container.py --base-url http://127.0.0.1:8000
Get-Content -Raw verify_postgres.py | kubectl --context kind-agent-relay exec -i deployment/agent-relay -- python -
kubectl --context kind-agent-relay exec deployment/postgres -- psql -U relay -d relay -c "SELECT 'agents' AS table_name, count(*) FROM agents UNION ALL SELECT 'tasks', count(*) FROM tasks UNION ALL SELECT 'attempts', count(*) FROM attempts; SELECT t.status, a.outcome, count(*) FROM tasks t JOIN attempts a ON a.task_id=t.id GROUP BY t.status,a.outcome;"
uv run pytest -q
git diff --check
```

For port 8080 instead, forward `8080:8000` and pass
`--base-url http://127.0.0.1:8080` to the verification script. Its default
remains port 8000. To visually verify the dashboard, use an agent token created
on this Kubernetes deployment, click **Use token**, then **Refresh**, and check
Agents, My tasks, completed output, and delivery history.

Cleanup (only when finished inspecting; these remove Kubernetes data):

```powershell
kubectl --context kind-agent-relay delete -f k8s/
kubectl --context kind-agent-relay delete secret postgres-credentials
kind delete cluster --name agent-relay
```

Stop port-forward with Ctrl+C. Resume the separate Compose setup with
`docker compose start` after freeing port 8000.

## Local CI/CD with GitHub Actions and act

`.github/workflows/ci.yml` runs checkout, locked Python/uv setup, all SQLite
tests, then the real HTTP acceptance flow and storage regressions against a
disposable PostgreSQL 16 container. The CI database uses temporary memory-backed
storage and is removed on exit; it never uses the Kubernetes database or PVC.
Only after all tests pass does the workflow build, load, and deploy an image.

Prerequisites: Docker Desktop, the existing `agent-relay` kind cluster,
kubectl, and act (`winget install --id nektos.act --exact`). From this repo:

```powershell
act --version
powershell -ExecutionPolicy Bypass -File ci/run-act.ps1
```

The launcher executes `act workflow_dispatch -W .github/workflows/ci.yml` with
the `catthehacker/ubuntu:act-22.04` runner, Docker socket access, and the `kind`
Docker network. It snapshots current uncommitted source into a temporary
checkout, excluding local databases, caches, and credentials. The file allowlist
is in `ci/run-act.ps1`; extend it when adding new source directories.

A separate temporary kubeconfig uses kind's internal API address
`agent-relay-control-plane:6443`, preserving TLS verification and the normal
host kubeconfig. It is mounted read-only and removed after act exits. Only run
trusted local workflows with this Docker/cluster access. No GitHub token or
cloud access is required. Hosted GitHub runs test/build only; the local kind
deployment steps explicitly require act and successful prior steps.

Each execution selects `agent-relay:<source-sha-prefix>-<random-uuid>`. The UUID
distinguishes uncommitted changes and repeated act runs with identical run IDs.
Tags are never reused by this workflow. It loads that exact tag with
`kind load docker-image`, verifies it with `crictl inspecti`, updates only the
app Deployment, waits for rollout, then checks `/health` and `/ready`.
PostgreSQL, its Secret, and its PVC are not reapplied or replaced.

If tests fail, all subsequent build/deploy steps are skipped and the current
version remains running. This was verified with a temporary failing test:
the Deployment image and pod UID remained unchanged. No failure test is retained.
Rollout failures fail the job visibly; there is no automatic rollback. The
existing Recreate strategy can briefly interrupt service during successful
updates. Run one local act invocation at a time (act does not enforce all GitHub
concurrency semantics).

After deployment, restart port-forward if its old pod was replaced:

```powershell
kubectl --context kind-agent-relay port-forward service/agent-relay 8000:8000
```

Open <http://127.0.0.1:8000/> and verify the heading **Agent Relay v2**. In a
second terminal:

```powershell
python verify_container.py
kubectl --context kind-agent-relay get pods,pvc
kubectl --context kind-agent-relay get deployment agent-relay -o wide
uv run pytest -q
git diff --check
```

The HTTP verification checks the served HTML against the local dashboard asset,
health/readiness, dashboard API calls, and the completed uppercase task flow.
