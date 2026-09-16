# Part 1 planning — operational code review

> Historical record. Current status and Prometheus/Grafana direction are documented
> in [production hardening](../planning/02-production-hardening.md).


## Goal and scope

The user requested a code review and documented findings as the first step of
production hardening. The initial stage produced review artifacts. Step 1 implementation
now resolves findings 004 and 012; the other fixes remain subsequent work. The overall delivery target is a GitHub repository using
Terraform for local infrastructure and ELK for observability.

## Approach

1. Read the assignment and current README to establish intended behavior.
2. Trace startup, authenticated request handling, cache access, rule matching,
   downstream forwarding, statistics, and shutdown.
3. Inspect the mock, load generator, dependencies, and tests for gaps that could
   undermine deployment verification.
4. Prioritize startup blockers, unbounded resource consumption, dependency
   failures, and incorrect forwarding before convenience improvements.
5. Record source evidence, production impact, a proposed solution, and acceptance
   checks for every issue in [the findings](../issues/README.md).
6. Run available checks and clearly distinguish static findings, local probes,
   and unperformed integration tests.

## Options considered and proposed decisions

- **Cache availability:** Prefer best-effort caching because stored values are
  decoded payloads, not authoritative records. A fail-closed policy would be
  justified only by an explicit requirement that processing depend on Redis.
- **Delivery semantics:** Preserve repeated forwarding of identical payloads.
  Cache deduplication of decoding does not establish exactly-once delivery.
- **Retries:** Prefer bounded work and explicit POST retry semantics. Blindly
  retrying a request after an ambiguous failure can duplicate delivery.
- **Rules:** Prefer startup validation and restart-based configuration changes
  for this assignment. Atomic live reload adds complexity without a stated need.
- **Metrics:** Prefer bounded counters/histograms that can be collected into
  Elastic, plus structured logs for investigation. Keeping every latency sample
  in application memory is unnecessary.
- **Readiness:** Separate liveness from readiness and align dependency checks
  with the chosen degraded-service policy; avoid restarting healthy processes
  merely because a downstream is unavailable.
- **Verification:** Use targeted regression tests for fixes and a separate real
  signed-protobuf/receipt check for deployment acceptance. A load script or a
  health endpoint alone cannot demonstrate correct delivery.

## AI-assisted workflow

The user first asked to read the DevOps assignment PDF and outline steps for a
GitHub submission using Terraform and ELK. That plan was saved in `me.txt`.
The next instruction was: "lets start working on 1, can you go through the code
and document findings ?"

The assistant traced the code, inspected source with line numbers, attempted the
existing tests, ran focused local probes, and drafted the issue records. Official
Redis, Python, and HTTPX documentation was consulted to verify library behavior;
links are included beside the relevant findings. No subagents were used.

The baseline test attempt was blocked by the system protobuf runtime being older
than required; this limitation is recorded rather than presenting the suite as
passing. Proposed solutions remain recommendations until implemented and tested.

## Step 1 implementation decisions — 2026-09-16

- Keep the documented `POKEPROXY_SECRET` name as the single supported interface;
  a legacy alias would add ambiguity without an established compatibility need.
- Require 32 decoded bytes and recommend random generation. Strict decoding and
  secret-aware storage replace permissive decoding and plain string storage.
- Remove the unused application port setting and document Uvicorn's existing
  port option instead of adding another startup entry point.
- Raise the protobuf lower bound rather than regenerate the schema with an older
  compiler. Keep the locked dependency versions; verify the minimum separately.
  Python has its own extended cross-version guarantees:
  https://protobuf.dev/support/cross-version-runtime-guarantee/
- Pin an isolated generator instead of adding compilation dependencies to the
  runtime. Verify generated output against the existing files.
- Add settings and lifespan regression coverage. Real Redis and forwarding
  acceptance tests belong to subsequent fixes and deployment verification.

The user authorized implementation with: "start working on steps 1 make sure
 everything is documented and all delivarables are set, i used git init command".
The assistant implemented these changes, installed dependencies, ran checks,
and recorded outcomes in `docs/verification/step-1.md`. No subagents were used.
