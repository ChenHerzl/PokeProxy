# Part 1 — production hardening

Completed: 2026-09-16. Basis: [initial assessment](01-initial-assessment.md).
Scope: application, mock, local traffic/verification tools, regression tests and
issue documentation. No Kubernetes, container build, GitOps, CI/CD, monitoring
stack or bootstrap implementation was started.

## What changed and why

The repository already had correct HMAC setting validation and a compatible
protobuf minimum. Those fixes were preserved. The remaining hardening follows
the existing FastAPI lifespan, router, models, Redis cache and rule parser rather
than replacing the framework or delivery architecture.

| Area / files | Change and purpose | Issue records |
| --- | --- | --- |
| `src/pokeproxy/main.py`, `proxy.py` | Reuse the shared HTTP client; one attempt with phase/total time budgets; bounded response and independent cleanup | 001, 011, 016 |
| `src/pokeproxy/cache.py` | One GET, validated cached JSON, bounded reads/writes, explicit best-effort fallback and last-operation health | 002, 003 |
| `src/pokeproxy/config.py`, `.env.example` | Positive finite operational settings, secret-aware Redis URL, prevent URL options bypassing budgets | 016, 017 |
| `src/pokeproxy/rules.py` | Strict startup validation, sanitized rule-index/field errors, restart-based configuration snapshot | 005 |
| `src/pokeproxy/http.py`, `proxy.py` | Chunked bounded body reads, deadline and header checks, safe correlation IDs, correct HTTP framing and cookie isolation | 006, 007 |
| `src/pokeproxy/stats.py`, `logging.py` | Fixed histograms/counters, consistent populations, JSON events, redacted errors, metrics endpoint | 008, 009, 010 |
| `mock_service/main.py` | Bounded input/receipt retention, JSON validation, per-run receipt lookup and health | 013 |
| `scripts/load_generator.py` | Validated inputs, monotonic pacing, achieved rate, failing exit status, environment secret option | 014, 017 |
| `scripts/verify.py` | Fixed signed protobuf and exact correlated downstream receipt check | 015 |
| `tests/test_hardening.py`, `test_runtime.py`, `test_tools.py`, updated `test_settings.py` | Failure-path, real socket, cache, observability, tooling and signal tests | All above |
| `pyproject.toml`, `uv.lock` | Add locked Prometheus client; preserve existing runtime dependency versions | 008, 010 |
| `.gitignore`, `README.md`, `docs/` | Ignore local secret variants, document operational contracts and each fix | 017 and all records |

The [issue index](../issues/README.md) links fifteen individual write-ups. Each
contains Problem, Production impact, Root cause, Solution, Alternatives considered,
and How to verify the fix. The pre-hardening assessment remains unchanged as
historical evidence. Earlier planning/verification files now point to this status.

## Decisions and alternatives

### HTTP forwarding and resource bounds

One shared HTTPX client per process preserves connection pooling. Every forward
has one attempt: the existing unlimited retry loop could repeat a POST after an
ambiguous timeout. Retry-on-connect-only was considered but precise pre-send
failure guarantees complicate the contract; idempotency-aware retries require
support not present downstream.

Phase timeouts govern individual HTTP I/O waits; an asyncio deadline caps the
entire downstream operation. These are distinct controls, as described by
[HTTPX timeout documentation](https://www.python-httpx.org/advanced/timeouts/).
Configuration defaults are documented in README, not production capacity claims.

The response path buffers at most the configured number of **raw encoded bytes**
and preserves Content-Encoding. This avoids HTTPX decoding a compressed body
while copying stale compressed metadata, and avoids proxy-side decompression
bombs. Decoded buffering was considered but needs decompression limits; streaming
all the way to the caller complicates error responses once headers are committed.
Content-Length is recalculated and hop-by-hop headers removed. Repeated response
headers remain distinct. The shared client rejects cookie retention to prevent
cross-caller session state and unbounded cookie accumulation.

An event-loop-local admission counter rejects overload immediately instead of
queuing arbitrarily many semaphore waiters. HTTP/Redis connection pools have the
same capacity. This counter covers handler work, not slow response transmission;
README's Uvicorn concurrency bound covers outstanding server tasks as a separate
layer. One worker per process is the supported operational model.

### Redis semantics

A cache hit still routes and forwards. A miss decodes and caches before routing,
just as before. TTL defaults to the original 300 seconds. Direct GET replaces KEYS;
SCAN or EXISTS would add work without useful information.

Redis is an optimization, not a delivery ledger. Reads/writes have explicit total
budgets and retries disabled; failures or corrupt cached JSON fall back to the
signed body. Fail-closed behavior would turn this optimization into a service-wide
outage dependency. No circuit breaker was added because it creates additional
state and recovery tuning without present evidence of need.

The configured Redis URL is secret-aware. Query options are rejected rather than
allowing redis-py's URL precedence to silently override connection budgets.
Only redis/rediss network URLs are supported by this simplified configuration
contract. Redis URLs and exceptions are never included in application events.

### Configuration and business behavior

Load rules once before clients are allocated or readiness becomes true. An
explicit empty rules list remains valid; a missing/malformed list fails startup.
Require fields and valid URL/header-safe metadata instead of coercing arbitrary
configuration types to strings. Configuration errors identify rule index and
field category without reproducing values. Restart-based change is simpler than
atomic live reload or introducing a configuration service.

AND conditions, first-match precedence, comparison operators, protobuf-to-Pydantic
JSON numeric representation, unmatched HTTP 200 `{}`, and downstream status/body
propagation are preserved. The mock's input schema is now validated because it
exists specifically to prove the Pokemon JSON contract.

### Lifecycle, signals and health

AsyncExitStack independently registers client cleanup as soon as resources are
acquired. Each close has its own budget and failure event, so one failed close
cannot suppress another. Partial initialization also closes acquired resources.
Cancellation is propagated, with in-flight counts released in finally blocks.

**No custom signal handler was added.** Inspection of the installed Uvicorn server
confirmed its SIGTERM/SIGINT handler, connection shutdown, graceful task wait,
forced task cancellation at its timeout, and subsequent lifespan shutdown. A
competing handler would risk breaking this sequence. Real subprocess tests prove
both signals drain a timed-out request and execute application cleanup.

The app becomes not-ready in lifespan teardown. Uvicorn has already stopped
accepting connections before that phase; we do not claim an early signal-driven
readiness transition. README specifies finite server drain time and explains how
to budget additional client close time. Service-discovery drain behavior belongs
to the future Kubernetes deployment and is not claimed tested here.

`/health` stays cheap. `/ready` checks initialized state and reports cache health
from the last cache operation: unknown, healthy or degraded. It performs no
network probe and does not claim up-to-the-instant dependency health. Redis and
downstream outages do not trigger restart loops or remove all replicas.

### Diagnostics and metrics

JSON stdout events use request IDs, rule indices, outcomes, durations and safe
exception types. Errors sent to clients include a correlation ID. There is no
logging of full payloads, HMAC keys/signatures, raw exception text or URLs.
Uvicorn's own output retains its normal formatting. An OpenTelemetry pipeline
and centralized logging would add scope without improving these initial hooks.

Native Prometheus counters/histograms replace the unbounded latency list. Each
lifespan owns an isolated registry, preventing duplicate registration in tests.
Inbound and forwarding populations are distinct; attempted bytes are explicitly
named/documented as attempts rather than delivery confirmations. `/stats` is
retained, but its keys intentionally change from URLs to `rule_N` for safe,
bounded labels and per-rule visibility. This diagnostics compatibility change
is documented in README. Rule reordering changes those index meanings.

The [Prometheus instrumentation model](https://prometheus.github.io/client_python/instrumenting/)
supports standard counters, histograms and gauges without inventing another
metrics format. CPU/memory collectors, scrape deployment, Grafana and alerts
remain Part 4.

### Mock, load generation and verification

Use one mock worker, bounded ephemeral receipts and filtered request-ID lookup.
A shared Redis/database receipt store would add dependencies and couple the
verification oracle to cache availability. Receipt eviction/restart can fail a
slow verifier; choose retention to fit demonstration traffic and query promptly.

The load script remains sequential. Monotonic pacing avoids adding a full interval
after each request; actual achieved rate is reported rather than claiming arbitrary
throughput. Inputs are validated and failures return nonzero. Async load generation
or a benchmark framework is unnecessary for a deterministic deployment gate.

The separate verifier sends a fixed matching Charizard, checks all JSON fields,
configured reason, request ID and receipt count, and never clears another run's
receipts. Health-only checks and random load generation cannot prove this contract.
Polling and individual HTTP operations have timeouts. The sync verifier is designed
for these bounded local services; a future CI job should also set a wall-clock job
timeout, since HTTPX inactivity timeouts alone are not a global bound against an
arbitrary peer trickling response bytes.

## Verification performed

Environment: Python 3.13.5; existing uv 0.10.9 at `/tmp/pokeproxy-uv/bin/uv`;
locked dependencies with added prometheus-client 0.26.0. The temporary real Redis
server used `redis:7.4.2`, pulled as digest
`sha256:fbdbaea47b9ae4ecc2082ecdb4e1cea81e32176ffb1dcf643d422ad07427e5d9`.
It was bound to a dynamically allocated localhost port with persistence disabled.
This is a test fixture version, not a production image/security recommendation.

| Check | Actual result |
| --- | --- |
| Initial request-hardening suite | 61 passed |
| Expanded lifecycle/tooling/real Redis suite | 80 passed |
| Cookie-isolation review run | 90 passed, 1 failed due to test calling nonexistent CookieJar.get_policy; corrected to exercise public cookie extraction behavior |
| Final complete suite, including real Redis and subprocess signals | **91 passed in 6.76 seconds; no skips** |
| `ruff check .` | **All checks passed** |
| `git diff --check` | **Passed** |
| `uv lock --check --offline` | **Passed; 36 packages resolved** |

Final complete-suite command in this session:

```bash
TEST_REDIS_URL=redis://127.0.0.1:32768/0 .venv/bin/pytest -q
```

Reproduce using your isolated Redis port and `uv run --frozen pytest -q`.
`TEST_REDIS_URL` is optional for routine unit/local-process runs; only the real
Redis expiry test skips if unset. The integration test uses and cleans one unique
cache key, never clears the database. Tests launch actual proxy/mock Uvicorn
processes over local sockets, verify receipts both with Redis available and
unreachable, demonstrate no-match HTTP 200 failing the verifier, and send both
SIGTERM and SIGINT during an in-flight downstream timeout. Partial startup,
close failure, cancellation, saturation, body boundaries, corrupted cache,
gzip framing, cookie isolation, metrics population and log redaction are also
covered. Signal tests use a reduced deadline for speed, not a capacity benchmark.

Sandbox permissions were needed for downloads, Docker, local sockets and process
verification. The temporary Redis container is removed after testing; test server
processes are cleaned up by fixtures. No deployment was created.

## Deliberately unchanged and remaining risks

- No idempotency, anti-replay protocol, durable queue or exactly-once promise.
  Ambiguous downstream failure plus caller retry can duplicate side effects.
- Redis cache writers and routing configuration authors are trusted. Schema checks
  do not prove cached content matches authenticated original bytes. Redis ACL/TLS,
  network isolation and production secret provisioning remain deployment work.
- HMAC rotation has no overlap/key-ID protocol. Public example keys are local only;
  key length is not an entropy check. Structured Pydantic error APIs can retain
  raw inputs; they must not be logged.
- `/metrics`, `/stats`, and mock receipt/reset endpoints are unauthenticated and
  need private exposure in the deployment. Client request IDs are not identities.
- Mock state/metrics are per-process; one worker is documented. Mock retention is
  bounded by count and per-request size, not durable or HA. Concurrent requests
  and response serialization still need appropriate server/container limits.
- Raw downstream response limits apply before client decompression. Slow clients
  require the documented server concurrency and eventual ingress time policies.
- Current cache GET still trusts Redis not to return an arbitrarily large value;
  network access restrictions and Redis memory/eviction policy are later work.
- Cleanup is bounded for ordinary cooperative async operations; an OS-level hang,
  event-loop-blocking extension or SIGKILL cannot be made graceful by Python.
- No throughput soak test, capacity sizing, internet-facing TLS test, vulnerability
  scan, Kubernetes drain test, production secret manager, dashboard or alert test
  is claimed. These are separate work, not implied by the regression result.
- Generator/build toolchain pinning from the previous assessment remains as-is;
  application runtime lock consistency is checked here.

## AI-assisted implementation flow

The user requested Part 1 based on the assessment, with code inspection before
changes, idiomatic fixes, one issue record per fix, tests and a decision record.
The assistant re-read application/lifecycle/config/cache/routing/stats code and
existing tests, then inspected the mock and load tool before editing them. It
implemented changes in those areas, expanded regression coverage, inspected the
installed server signal implementation, checked primary timeout/client/metrics
documentation, and ran the real Redis and process-level tests. Test findings and
cookie retention review informed the final changes. No subagents were used.
