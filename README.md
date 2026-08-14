# Agents Office

Teams of AI agents with distinct roles that work on one task together — in a
pipeline, under a supervisor, arguing it out before a judge, or handing work to
each other. Models come from **Anthropic, OpenAI, xAI and Google**, running on
either the platform's provider keys or the customer's own.

```
Browser ──▶ Caddy ──┬──▶ Next.js (UI, React Flow editor, live run view)
                    └──▶ FastAPI ──┬──▶ PostgreSQL
                                   ├──▶ Redis ──▶ ARQ workers ──▶ providers
                                   └──▶ SSE stream back to the browser
```

## Contents

- [Stack](#stack)
- [Layout](#layout)
- [Running locally](#running-locally)
- [Deploying](#deploying)
- [Topologies](#topologies)
- [Tools](#tools)
- [Models and providers](#models-and-providers)
- [Key modes and BYOK](#key-modes-and-byok)
- [Cost control and billing](#cost-control-and-billing)
- [Security](#security)
- [Operations](#operations)
- [Testing](#testing)
- [Not built yet](#not-built-yet)

## Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI (async) | SSE, long-lived streams, one language with the workers |
| UI | Next.js 15 App Router, Tailwind 4, React Flow | Same origin as the API, so the refresh token can be an httpOnly cookie |
| Orchestration | Custom engine + topology presets | Budget, cancellation and cost accounting live in the step executor, not in each topology |
| Queue | ARQ on Redis | Runs take minutes; nothing long-lived belongs in an HTTP handler |
| Events | Redis pub/sub + capped replay list | Reconnect with `Last-Event-ID` and lose nothing |
| Database | PostgreSQL, SQLAlchemy 2.0 async, Alembic | Ledger and audit trail need real transactions |
| Providers | `anthropic`, `openai`, `google-genai` | xAI and OpenRouter speak the OpenAI wire format |
| Secrets | AES-256-GCM envelope encryption with key rotation | Customer credentials are the highest-value data here |

## Layout

```
core/
  config.py            settings; refuses to start in production without secrets
  crypto.py            envelope encryption + KEK rotation for BYOK keys
  security.py          Argon2id passwords, JWT access tokens, refresh rotation
  models.py            full schema (14 tables)
  billing.py           append-only credit ledger
  ratelimit.py         Redis sliding window + concurrency slots (Lua)
  events.py            run event bus, token buffering, cancellation flag
  artifacts.py         extract labelled files out of agent output
  tools/               tool contract, registry, artifact + network tools
  audit.py             security event log
  runner.py            DB <-> engine glue; owns the run's terminal state
  llm/
    registry.py        model -> provider, price, capabilities
    pricing.py         micro-cent accounting, DB price overrides
    keys.py            managed / byok / hybrid credential resolution
    router.py          provider selection, retries, cost attribution
    providers/         anthropic, openai-compatible (OpenAI/xAI/OpenRouter), google
  orchestration/
    state.py           shared board, artifact index, transcript compaction
    budget.py          step / cost / time ceilings and cancellation
    engine.py          executes one agent turn and its tool loop
    presets.py         pipeline, supervisor, debate, blackboard, swarm, custom
api/                   routers, schemas, auth dependencies, middleware
worker/                ARQ worker, cron sweeps, Prometheus metrics
web/                   Next.js app
migrations/            Alembic
scripts/               seed, key rotation, superuser
tests/                 217 tests
```

## Running locally

```bash
docker compose up -d
```

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Create `.env` from the example, then generate the two required secrets:

```bash
python -m scripts.rotate_keys --generate
```

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the first in `MASTER_KEYS`, the second in `JWT_SECRET`, add at least one
provider key, then:

```bash
alembic upgrade head
```

```bash
python -m scripts.seed --email you@example.com --password 'your-long-passphrase'
```

Three processes:

```bash
uvicorn api.main:app --reload
```

```bash
arq worker.main.WorkerSettings
```

```bash
cd web && npm install && npm run dev
```

The UI is at `http://localhost:3000`; it proxies `/api` to the backend so the
browser sees one origin.

## Deploying

Single VPS, everything in Docker, TLS issued automatically:

```bash
cp .env.example .env
```

Fill in `MASTER_KEYS`, `JWT_SECRET`, `POSTGRES_PASSWORD`, `DOMAIN` and the
provider keys, then:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Caddy terminates TLS and routes `/api/*` to FastAPI and everything else to
Next.js. Migrations run once, from the API container (`RUN_MIGRATIONS=true`), so
replicas cannot race each other. Workers scale with `WORKER_REPLICAS`.

Postgres and Redis are not published to the host — only the compose network
reaches them.

## Topologies

All six run on one engine. A preset decides only *who speaks next and what they
are told*; budget, cancellation, streaming, artifacts and persistence belong to
the engine. Adding a topology is one async function plus a validator.

| Preset | Shape | Graph keys |
|---|---|---|
| `pipeline` | Linear hand-off | `nodes` |
| `supervisor` | A manager delegates until the job is done | `supervisor_agent_id`, `workers`, `max_rounds` |
| `debate` | N agents argue R rounds, a judge rules | `debaters`, `judge_agent_id`, `rounds` |
| `blackboard` | A planner posts a dependency-ordered task list | `planner_agent_id`, `workers`, `max_tasks` |
| `swarm` | Peer-to-peer hand-off | `entry_agent_id`, `agents`, `max_hops` |
| `custom` | Arbitrary graph with agent and router nodes | `start`, `nodes`, `edges` |

Graphs are validated when a workflow is **saved** and again when a run is
**submitted** — a topology that references a deactivated agent is rejected
before anyone pays for a step.

Agents emit deliverables as labelled blocks, which become versioned artifacts:

````
```python path=src/app.py
...
```
````

Model output is untrusted, so absolute paths and `..` traversal are rejected
rather than cleaned.

## Tools

Agents hold an allowlist of tools. The engine runs the call loop; topologies
know nothing about it, so every topology gets tools for free.

| Tool | Side effect | What it does |
|---|---|---|
| `list_artifacts` | reads run state | What the run has produced so far |
| `read_artifact` | reads run state | Read a file, optionally by line range |
| `write_artifact` | writes | Create or replace a file, versioned |
| `edit_artifact` | writes | Replace an exact, unique substring |
| `web_fetch` | network | Fetch a URL as readable text |
| `web_search` | network | Ranked results — only when a search provider is configured |

Given write tools, an agent stops pasting whole files into its reply and works
on them directly: read, targeted edit, write back. That is cheaper and avoids
the failure where a model asked to "keep the rest" quietly drops half a file.

**Loop safety.** Every model call in a tool loop is checked against the run's
step, cost, time and cancellation guards *before* it is made. On the last
permitted iteration the tools are withdrawn, which forces the model to answer
with what it has rather than requesting a call it will not get. Parallel calls
are capped per turn; read-only calls run concurrently, writes run in order so
two edits to one file cannot interleave.

**Failure is a result, not an exception.** A hallucinated tool name, a bad
argument, a timeout or a crash all come back to the model as a readable error it
can recover from — none of them kill the run.

**Network safety.** `web_fetch` is the realistic SSRF surface, because a model
can be talked into fetching a URL by anything it reads. The guard runs per
redirect hop: http/https only, hostname resolved and every address checked
against the private, loopback, link-local and reserved ranges, cloud-metadata
hosts refused by name as well as by range, responses capped and time-bounded.
Error messages never echo the resolved address, so a blind probe learns nothing.
Optional per-deployment allow and deny lists layer on top.

Every call is recorded in `tool_calls` with its arguments and result. Arguments
are scrubbed on the way out — a model can be talked into putting a credential in
one, and the audit trail must not become the leak.

Turn the whole surface off with `TOOLS_ENABLED=false`, or just the network tools
with `TOOLS_NETWORK_ENABLED=false`.

## Models and providers

| Provider | Reasoning control | Notes |
|---|---|---|
| Anthropic | `thinking: adaptive` + `output_config.effort` | Prompt caching on the system prompt |
| OpenAI | `reasoning_effort` | `xhigh`/`max` map to `high` |
| xAI (Grok) | `reasoning_effort` where supported | OpenAI-compatible wire format |
| Google (Gemini) | `thinking_config.thinking_budget` | Effort maps to a token budget |

One canonical effort scale (`low`…`max`) is translated per vendor in
`core/llm/registry.py`. A model that is not in the registry is rejected at agent
creation: an unpriced model cannot be billed correctly.

Prices ship as defaults in the registry and are overridable at runtime through
the `model_prices` table (`PUT /admin/model-prices`), because vendors change
rates on their own schedule and that should not require a redeploy.

## Key modes and BYOK

- **managed** — the platform's keys; the organisation is billed in credits.
- **byok** — the organisation's own keys; the platform bills nothing for tokens.
- **hybrid** — their keys for expensive reasoning models, ours for cheap utility
  models (`UTILITY_MODELS` in `core/llm/keys.py`).

Customer keys are encrypted with a per-secret data key, which is itself wrapped
with a versioned master key. The organisation id is the AEAD associated data, so
a row copied into another tenant fails to decrypt. Only a mask
(`sk-ant…4f2a`) and a fingerprint ever leave the server.

Rotating the master key:

```bash
python -m scripts.rotate_keys --generate
```

Add the new key to `MASTER_KEYS` alongside the old one, bump
`MASTER_KEY_VERSION`, restart, then:

```bash
python -m scripts.rotate_keys --apply
```

Remove the old key once `--status` reports nothing outstanding.

## Cost control and billing

Runaway agent loops are the main way to lose money by accident, so every run
carries a step ceiling, a cost ceiling and a wall-clock ceiling. A workflow can
ask for less than the platform maximum, never more, and the guards are evaluated
*before* each step — once a provider call is made the money is spent.

Costs accumulate in micro-cents and are rounded to whole cents exactly once, at
the ledger. Rounding is upward: a run that costs a fraction of a cent still cost
the platform money.

The credit ledger is append-only and is the source of truth for a balance;
`orgs.credits_cents` is a cached projection written in the same transaction,
with `POST /orgs/{id}/billing/reconcile` to re-derive it. Entries are idempotent
per key, so a redelivered job cannot double-charge — and a run that fails
half-way is still billed for the steps that completed, because the provider
already charged us for them.

## Security

- **Passwords** — Argon2id, 64 MiB, transparent rehash when parameters change.
- **Access tokens** — short-lived JWTs carrying org memberships. A password
  change or "sign out everywhere" bumps a token epoch, invalidating outstanding
  tokens without a per-request revocation lookup.
- **Refresh tokens** — random, stored only as SHA-256, single-use. Replaying a
  spent token revokes the whole family; that revocation is committed even though
  the request fails with 401.
- **Tenant isolation** — membership is re-read from the database on every
  request rather than trusted from the token, and a foreign organisation returns
  404 rather than 403 so ids cannot be enumerated.
- **Login** — identical response for an unknown address and a wrong password;
  rate limited per address and per source address.
- **Secret hygiene** — a logging processor redacts anything shaped like a
  provider key or bearer token before it reaches a handler; the audit log
  scrubs sensitive keys.
- **Audit trail** — written in the same transaction as the change it describes,
  so the two cannot diverge.
- **Transport** — HSTS, `nosniff`, `DENY` framing, no-referrer, request-body cap,
  strict CORS, and an explicit trusted-host list.

## Operations

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness — no dependencies, so a database blip does not restart healthy processes |
| `GET /health/ready` | Readiness — checks Postgres and Redis, 503 when degraded |
| `GET /metrics` | Prometheus (API); workers expose their own on `:9100` |

Every response carries `X-Request-Id`, and every log line for that request
carries the same id.

Two cron sweeps run in the worker: one fails runs whose worker died without
reporting completion, the other re-derives concurrency counters from the
database so a crashed worker cannot leak a slot permanently.

## Testing

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/python -m ruff check .
```

```bash
cd web && npm run typecheck && npm run build
```

217 tests cover the crypto envelope and rotation, artifact path safety, budget
and cancellation guards, all six topologies and their validators, the tool loop
and its ceilings, the SSRF guard, the credit ledger, auth and token rotation,
tenant isolation, and end-to-end run execution against a fake provider. The
suite runs on in-memory SQLite and fake Redis, so it needs no services.

## Not built yet

- **Payments.** The ledger and top-up endpoint exist; a payment provider would
  post through the same `billing.post_entry` path. Deliberately out of scope for
  this iteration.
- **Email.** No verification, password reset or invitations — adding a member
  requires the account to exist already.
- **Mobile app.** The API is shared and ready for it.
