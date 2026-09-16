> Historical pre-hardening review. Current fixes and results are in
> [Part 1 production hardening](02-production-hardening.md).

# Part 1 — Code review findings

Reviewed: 2026-09-15. Status updated 2026-09-16: **004 and 012 fixed and verified; all other findings remain open**.

Original evidence below describes the pre-fix snapshot. See
[step 1 verification](../verification/step-1.md) for implemented decisions and results.

Scope: application source, mock downstream, load generator, tests, configuration,
README, dependency declaration, and lock file. Generated protobuf was inspected
for runtime compatibility, not edited. Infrastructure and CI implementation are
covered by later assignment parts.

Priorities: **P1** blocks startup or threatens availability/correctness under an
ordinary failure or sustained traffic; **P2** affects operability, validation, or
the reliability of the delivery demonstration. These are review priorities, not
measured incident severities. Source references describe the pre-fix snapshot.

## Summary

| ID | Priority | Finding |
|---|---|---|
| [001](#001--unbounded-downstream-retries-and-unclosed-clients) | P1 | Unbounded downstream retries and unclosed clients |
| [002](#002--every-cache-read-enumerates-redis-keys) | P1 | Every cache read enumerates Redis keys |
| [003](#003--cache-failures-become-request-failures) | P1 | Cache failures become request failures |
| [004](#004--secret-configuration-breaks-documented-startup) | P1 | Secret configuration breaks documented startup |
| [005](#005--routing-configuration-is-read-and-validated-on-every-request) | P1 | Routing configuration is read and validated on every request |
| [006](#006--request-limits-and-header-validation-are-incomplete) | P1 | Request limits and header validation are incomplete |
| [007](#007--forwarded-headers-can-disagree-with-the-body) | P1 | Forwarded headers can disagree with the body |
| [008](#008--latency-statistics-grow-without-a-bound) | P1 | Latency statistics grow without a bound |
| [009](#009--traffic-and-error-statistics-are-incorrect) | P2 | Traffic and error statistics are incorrect |
| [010](#010--application-observability-is-insufficient) | P2 | Application observability is insufficient |
| [011](#011--readiness-and-cleanup-do-not-cover-failure-paths) | P1 | Readiness and cleanup do not cover failure paths |
| [012](#012--declared-protobuf-minimum-is-incompatible-with-generated-code) | P1 | Declared protobuf minimum is incompatible with generated code |
| [013](#013--mock-receipt-storage-is-unbounded-and-process-local) | P2 | Mock receipt storage is unbounded and process-local |
| [014](#014--load-generator-cannot-prove-throughput-or-delivery) | P2 | Load generator cannot prove throughput or delivery |
| [015](#015--tests-do-not-cover-the-request-lifecycle) | P2 | Tests do not cover the request lifecycle |

## 001 — Unbounded downstream retries and unclosed clients

**Evidence:** `src/pokeproxy/proxy.py:54-66`, `:81-105`;
`src/pokeproxy/main.py:24-27`.

**Problem:** `_forward_with_retry` loops forever on connection errors and timeouts.
Each attempt constructs an `AsyncClient(timeout=600.0)` without closing it. The
shared lifespan client, with shorter timeouts, is never used for forwarding.
Caught timeouts never reach the handler that would return 504. A read timeout
can also occur after a downstream has already accepted the POST, so retrying can
duplicate side effects.

**Production impact:** A downstream outage accumulates long-running requests,
clients, and connections. There is no application retry deadline; draining a
deployment can stall. Delivery can be duplicated after an ambiguous timeout.

**Proposed solution:** Pass the shared client into forwarding; configure phase
timeouts, an overall deadline, and connection/concurrency limits. Default to no
automatic retry of ambiguous POST failures unless the downstream supports an
idempotency key. If retrying safe failures, cap attempts and backoff. Preserve
cancellation and return controlled 502/504 responses.

**Acceptance checks:** Simulate unavailable downstream, repeated timeout, and
accept-then-timeout. Assert bounded completion, documented attempt counts,
client reuse/closure, and cancellation during shutdown.

## 002 — Every cache read enumerates Redis keys

**Evidence:** `src/pokeproxy/cache.py:14-24`.

**Problem:** The caller already knows the full key, but reads use
`KEYS pokeproxy:pokemon:*`, scan the result in Python, then call GET.

**Production impact:** Request work grows with the database keyspace; KEYS can
block other Redis operations and returns an unnecessary list to the proxy.

**Proposed solution:** Call `GET(cache_key)` directly. SCAN is also unnecessary
for looking up a known key. Preserve the existing five-minute TTL unless a
different retention requirement is established.

**Acceptance checks:** Assert that a hit and miss each issue one GET and no
KEYS/SCAN. Verify expiry using Redis in an integration test.

**Validation:** A mocked Redis probe confirmed KEYS followed by GET. Redis
documents KEYS as O(N): [official reference](https://redis.io/docs/latest/commands/keys/).

## 003 — Cache failures become request failures

**Evidence:** `src/pokeproxy/cache.py:18-31`; `src/pokeproxy/proxy.py:142-153`;
`src/pokeproxy/main.py:29`.

**Problem:** Redis command failures, invalid cached JSON, and invalid cached
Pokemon fields escape the request handler. A failed cache write prevents a
successfully decoded payload from being forwarded. Redis timeout/retry budgets
are not explicitly configured by the application.

**Production impact:** An optimization becomes a dependency that can stop all
authenticated traffic. A corrupt cache entry repeatedly breaks its payload
until expiry. Dependency defaults determine how long failures take.

**Proposed solution:** Treat this decoded-payload cache as best effort: set
explicit budgets, catch expected Redis errors, and continue using the original
payload on read/write failures. Validate cached data and treat invalid entries
as misses; optionally remove them. Log and count degraded operation.

**Acceptance checks:** Cover read failure, write failure, timeout, malformed JSON,
and schema mismatch. Verify forwarding still succeeds within the chosen budget.

**Validation:** A mocked invalid cached value raised uncaught `JSONDecodeError`.

**Semantic boundary:** Cache hits currently skip decoding but still match and
forward. This is not delivery deduplication. The README request flow supports
this behavior; do not silently turn it into suppression of repeated deliveries.
Document the contract before implementing any idempotency mechanism.

## 004 — Secret configuration breaks documented startup

**Resolution (2026-09-16):** Fixed. Settings now reads `POKEPROXY_SECRET` as
`SecretStr`, validates strict base64 and at least 32 decoded bytes before client
creation, and hides inputs in human-readable errors. Updated the public example
and load-generator key. Removed the unused port setting; Uvicorn controls the
listener. Regression tests cover missing/invalid/short secrets, redaction, and
startup from the example dotenv. See the verification record for limits.

**Evidence:** `src/pokeproxy/config.py:13-25`; `.env.example:1`;
`README.md:32-34,52-55`; `src/pokeproxy/main.py:18-21`.

**Problem:** README and the example use `POKEPROXY_SECRET`, while Settings requires
`pokeproxy_hmac_key`, corresponding to `POKEPROXY_HMAC_KEY`. There is no alias.
Base64 decoding is permissive and permits an empty decoded key. The secret uses
a plain string field rather than a secret-aware representation.

**Production impact:** Following the documented configuration leaves a required
field missing at startup. If the actual key variable contains empty or certain
malformed values, an empty HMAC key can be accepted. Plain secret fields are
easier to expose accidentally in configuration representations/error output.

**Proposed solution:** Choose one documented environment name and explicitly
support it. Strictly validate base64 and a documented key-strength policy;
reject empty keys. Use a secret-aware field and redact validation input. Keep the
public development example clearly marked as development-only.

**Acceptance checks:** Start with the documented variable; reject missing,
empty, malformed, and insufficient keys with useful redacted errors. Check
configuration representations do not contain the supplied secret.

**Validation:** Python's current decoding behavior turns `!!!!` into empty bytes.
[Base64 documentation](https://docs.python.org/3/library/base64.html)

**Related configuration gap:** `pokeproxy_port` is declared but never consumed;
the README starts Uvicorn with a literal port. Either wire the setting into an
entry point or remove the misleading promise that it controls the listener.

## 005 — Routing configuration is read and validated on every request

**Evidence:** `src/pokeproxy/proxy.py:155`; `src/pokeproxy/rules.py:110-135`;
`src/pokeproxy/main.py:18-32`.

**Problem:** Every authenticated, decoded request performs synchronous file I/O,
JSON parsing, and rule construction inside an async handler. Startup does not
validate the file. File/JSON/schema errors escape as request failures. A missing
`rules` field silently becomes an empty list; URL validation checks only a prefix,
so `http://` is accepted. Reasons are not validated for safe HTTP header use.

**Production impact:** Configuration errors appear only under traffic; a typo can
return 200 while forwarding nothing. Disk access blocks other work on the event
loop. Invalid URLs or header values fail only when a rule matches.

**Proposed solution:** Validate a strict rules schema at startup, including a
usable HTTP(S) URL and header-safe reason. Load an immutable in-memory ruleset.
Use restart-based configuration changes initially; if live reload is required,
validate a complete candidate and atomically replace the last known good rules.
Define whether an explicitly empty rules list is allowed.

**Acceptance checks:** Startup rejects missing files, broken JSON, wrong container
types, absent required fields, invalid URLs, and unsafe reasons. Verify AND and
first-match semantics and that request handling does not read the file.

## 006 — Request limits and header validation are incomplete

**Evidence:** `src/pokeproxy/proxy.py:36-38,114-136`.

**Problem:** Requests without an oversized Content-Length are fully buffered by
`request.body()` before the size check. The integer conversion has no malformed
input handling. Signature comparison accepts an arbitrary string; non-ASCII
strings can raise TypeError in `hmac.compare_digest` instead of returning 401.

**Production impact:** Missing-length/chunked requests can consume much more than
the intended 1 MiB before rejection. Bad headers reaching the application can
cause 500 responses. Server-level header rejection varies; it is not a substitute
for the application's bounded body handling.

**Proposed solution:** Read chunks and stop as soon as the accumulated limit is
exceeded. Validate length values and require the agreed SHA-256 hex signature
format before constant-time comparison. Configure an appropriate ingress/server
request-time budget to address slow uploads.

**Acceptance checks:** Cover oversized bodies with and without Content-Length,
chunked boundary cases, malformed/negative length, and missing, wrong-length,
non-hex, and non-ASCII signatures. Assert controlled 4xx responses and early stop.

## 007 — Forwarded headers can disagree with the body

**Evidence:** `src/pokeproxy/proxy.py:22-51,89-93`.

**Problem:** Responses copy all downstream headers onto `resp.content`, although
HTTPX can decompress that content. A gzip response can retain its original
Content-Encoding and compressed Content-Length after its bytes have changed.
Request forwarding leaves hop-by-hop headers and headers named by Connection.
It also leaves Content-Encoding although the outgoing body is newly generated
JSON. A client-supplied lowercase `x-grd-reason` can coexist with the added
`X-Grd-Reason` in the Python dictionary.

**Production impact:** Clients can receive incorrectly framed or mislabeled
responses. Downstreams can see inappropriate transport headers, incorrect body
encoding metadata, or ambiguous routing reasons.

**Proposed solution:** Filter hop-by-hop headers in both directions, including
Connection-nominated fields. Normalize header names and overwrite proxy-owned
headers case-insensitively. For decoded response bodies, remove stale encoding
and length metadata and let the response layer calculate length; alternatively
stream original raw bytes with correct lifecycle handling. Set a response-size
budget or use bounded streaming rather than unconditionally buffering arbitrary
downstream bodies.

**Acceptance checks:** Verify gzip response bytes and headers agree, connection
headers do not cross the proxy, and spoofed reason headers cannot override the
configured reason. Include oversized downstream responses.

**Validation:** An HTTPX probe decoded a compressed 36-byte response to
`hello downstream` while preserving `content-encoding: gzip` and length 36.
[HTTPX async and raw-stream documentation](https://www.python-httpx.org/async/)

## 008 — Latency statistics grow without a bound

**Evidence:** `src/pokeproxy/stats.py:15-19`;
`src/pokeproxy/proxy.py:106-108`.

**Problem:** Every completed forwarding attempt inserts a latency into a sorted
list retained for the process lifetime. List insertion also moves existing
elements. Current `/stats` output does not expose these latency values.

**Production impact:** Memory increases with total traffic, and insertion work
can become more expensive as the process ages, eventually risking OOM and
event-loop latency.

**Proposed solution:** Replace lifetime samples with bounded histogram buckets
for exported metrics. Use a bounded window only if local percentile reporting
is still required. Keep labels bounded by configured rule identifiers.

**Acceptance checks:** Feed a large sequence of samples and verify storage is
bounded independently of request count; verify bucket counts and duration units.

**Validation:** 10,002 recorded samples retained 10,002 entries.

## 009 — Traffic and error statistics are incorrect

**Evidence:** `src/pokeproxy/proxy.py:84-109,161-162`;
`src/pokeproxy/stats.py:21-44`.

**Problem:** `bytes_received` is overwritten instead of accumulated.
`request_count` increases only when a downstream response arrives, but transport
failures increase `error_count` and add elapsed time. Consequently the numerator
and denominator describe different populations. `bytes_sent` records one payload
even when retries may send it more than once, or delivery never occurs.

**Production impact:** Error rate can exceed 100%, report zero during an all-
transport-failure period, and distort average latency. Byte statistics are not
reliable throughput measurements.

**Proposed solution:** Define separate inbound request, logical forwarding, and
downstream attempt counters. Increment each exactly once at its lifecycle point;
observe durations for the same population. Accumulate byte totals and name them
according to whether they measure attempted or confirmed work.

**Acceptance checks:** Mix success, downstream HTTP error, transport error,
unmatched input, and retries. Check totals, a bounded error fraction, latency
denominators, and cumulative byte accounting.

**Validation:** A registry populated to represent one failed HTTP response plus
one transport failure reported `request_count=1`, `error_count=2`, error rate
2.0, and average 3 seconds for durations of 1 and 2 seconds.

## 010 — Application observability is insufficient

**Evidence:** `src/pokeproxy/main.py:47-50`; `src/pokeproxy/stats.py:38-56`;
`src/pokeproxy/proxy.py:112-168`; no application logger calls in reviewed source.

**Problem:** There is no application structured logging or request correlation.
`/stats` exposes only process-local per-URL totals, omits latency, and misses
invalid signatures, decode failures, cache failures, and unmatched requests.
There is no standard metrics endpoint or resource telemetry configuration.

**Production impact:** Operators cannot reliably distinguish upstream input
problems, Redis problems, and downstream outages. Replica-local counters are
easy to misinterpret as service-wide measurements. The observability assignment
requirements are not yet met.

**Proposed solution:** Add JSON events with request ID, outcome, configured rule
ID, status, and duration. Exclude secrets, signatures, and full payloads. Export
bounded-label counters and histograms for application behavior; collect resource
metrics through the cluster monitoring stack. Feed logs through Logstash and
metrics through an Elastic-compatible collector into Elasticsearch/Kibana.

**Acceptance checks:** A request can be correlated across its lifecycle without
sensitive values in logs. Success and each error class update the correct
metrics. Later deployment work must prove dashboard and alert ingestion.

## 011 — Readiness and cleanup do not cover failure paths

**Evidence:** `src/pokeproxy/main.py:16-44`; finding 001.

**Problem:** `/health` always returns alive, and there is no separate readiness
check. Rules are not validated at startup and Redis is not exercised there.
Cleanup after yield is not protected by try/finally; a Redis close exception
prevents HTTP-client closure. Partially initialized resources are not protected.
Forwarding clients from finding 001 are outside this cleanup entirely.

**Production impact:** An unconfigured service can appear healthy before serving
failing traffic. Exceptional startup/shutdown can leave resources unclosed;
unbounded forwarding can interfere with draining.

**Proposed solution:** Keep liveness cheap. Add readiness tied to successfully
loaded configuration and initialized resources. If Redis becomes best effort,
report degraded cache health without making it a mandatory readiness dependency.
Use AsyncExitStack or reliable nested cleanup for clients. Bound request work
and document server termination grace and draining behavior.

**Acceptance checks:** Invalid configuration prevents readiness/startup; dependency
degradation follows the documented policy. Inject initialization and close
failures and verify acquired resources close. Verify termination under traffic.

## 012 — Declared protobuf minimum is incompatible with generated code

**Resolution (2026-09-16):** Fixed. Raised the runtime minimum to 6.31.1 and
refreshed `uv.lock`, retaining locked protobuf 7.34.0. All 15 tests pass with
the locked environment and in an isolated minimum-runtime environment.
`scripts/generate_proto.sh` pins grpcio-tools 1.74.0 (libprotoc 31.1); its output
exactly matches both checked-in generated files.

**Evidence:** `pyproject.toml:11`; `src/pokeproxy/proto/pokemon_pb2.py:5-19`;
`uv.lock:261-263`.

**Problem:** The project permits `protobuf>=5.29`, but generated code imports the
runtime version checker and requests generated-code version 6.31.1. The dependency
range therefore permits older runtimes that cannot satisfy that requirement.

**Production impact:** An installation satisfying project metadata can fail at
import/startup. The existing lock selects 7.34.0; this is a dependency-range
defect, not proof that the locked installation fails.

**Proposed solution:** Align the runtime lower bound with the checked-in generated
code, or regenerate with an intentionally supported toolchain. Document and pin
the generation toolchain; use frozen lock installation in CI and containers.

**Acceptance checks:** Test imports against the lowest supported runtime and the
locked environment, then run the full suite. Regeneration should be reproducible.

**Validation limitation:** The available system runtime is 4.21.12, outside even
the current declared range. Its import failure does not reproduce the allowed-
version defect; it only explains why the baseline suite could not run here.

## 013 — Mock receipt storage is unbounded and process-local

**Evidence:** `mock_service/main.py:9-28`.

**Problem:** Every receipt is appended to an unlimited in-memory list. `/received`
returns the entire list. State is isolated per worker/replica and lost on restart;
DELETE clears all receipts, including those from concurrent verification runs.

**Production impact:** Sustained demonstration traffic can exhaust mock memory.
Deployment verification becomes unreliable if receipt writes and reads reach
different workers or overlapping runs erase one another's evidence.

**Proposed solution:** For this assignment, use one mock replica/worker, bounded
retention, and per-run correlation/querying. Keep administrative receipt endpoints
internal. Document that this is a verification service with ephemeral state.

**Acceptance checks:** Storage remains bounded under load and verification finds
its own receipt without clearing other runs. Document restart limitations.

## 014 — Load generator cannot prove throughput or delivery

**Evidence:** `scripts/load_generator.py:71-79,90-120`; `README.md:91-94`.

**Problem:** Requests run sequentially with a full `1/rps` sleep after each response,
so request duration is added to the target interval. Zero RPS divides by zero;
negative inputs are not validated. HTTP errors are counted but do not set a
failing process exit status. A 200 response is not checked against downstream
receipts. README's extra `cd app` does not match this extracted repository root.

**Production impact:** The stated load can greatly exceed actual generated load,
masking capacity issues. The script cannot serve as a deployment acceptance gate.

**Proposed solution:** Validate inputs; use monotonic pacing and bounded concurrency
if controlled load is needed. Report actual achieved rate. Implement a separate
deterministic signed-protobuf end-to-end check that verifies the downstream JSON
and exits nonzero on mismatch. Correct the working-directory instructions.

**Acceptance checks:** Reject invalid inputs; measure achieved rate against a
delayed mock. Make the end-to-end check fail for no match, wrong payload, missing
receipt, and downstream outage.

## 015 — Tests do not cover the request lifecycle

**Evidence:** `tests/test_basic.py:31-91`.

**Problem:** The five existing tests cover decoding and basic rule behavior only.
They do not invoke `/stream`, startup/shutdown, Redis behavior, forwarding, error
handling, or statistics. There is no automated downstream receipt assertion.

**Production impact:** Passing this suite would not protect the main failure
paths identified above or prove that a deployed version forwards traffic.

**Proposed solution:** Add regression tests alongside fixes, using deterministic
HTTP/Redis doubles for failure paths, real Redis integration for cache semantics,
and a deployed end-to-end receipt check. Preserve existing positive tests.

**Acceptance checks:** Each fixed P1 issue has a meaningful regression check.
CI runs the supported locked environment and a real deployment verification gate.

## Review validation and limitations

- Static review covered all handwritten application modules, the mock, load
  generator, all five tests, examples, README, and dependency metadata.
- `PYTHONPATH=src python3 -m pytest -q` stopped during collection: system protobuf
  4.21.12 does not provide the imported `runtime_version`. No tests passed or ran.
  Pytest also warned that `asyncio_mode` was unknown, indicating the async plugin
  was not available in this system environment. `uv` was not installed.
- Focused local probes used the actual cache/stats modules, mocked Redis, the
  installed HTTPX 0.28.1, and Python 3.13.5. They confirmed the behaviors noted
  under findings 002, 003, 004, 007, 008, and 009. They were not a live Redis or
  end-to-end deployment test.
- No load benchmark, cluster deployment, or shutdown-under-traffic experiment was
  performed. Proposed acceptance checks above remain work for the fix phase.
- This folder currently has no Git repository metadata. Review documents are
  local files, ready to include when the GitHub repository is initialized.

## Existing behavior worth preserving

- HMAC uses constant-time comparison for valid ASCII inputs and is checked before
  cache access and protobuf processing.
- Protobuf decode errors and empty names are already mapped to invalid input.
- Cache writes already have a TTL; missing expiry is not a finding.
- Rule matching implements AND conditions and first-match wins without eval.
- The application already has a lifespan hook and a shared HTTP-client design;
  the forwarding path should use it.
- `.env` is ignored and a dependency lock file already exists.

## Suggested fix order

1. Restore startup/dependency consistency (004, 012) and establish runnable tests.
2. Bound downstream work and ensure cleanup (001, 011).
3. Make cache access direct and resilient (002, 003).
4. Validate configuration and HTTP boundaries (005, 006, 007).
5. Correct bounded metrics and structured logging (008, 009, 010).
6. Strengthen the mock, load tooling, and lifecycle coverage (013, 014, 015).

For each issue, replace the proposed solution with the implemented decision and
record actual verification results when the fix lands.
