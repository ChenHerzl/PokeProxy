# Add structured diagnostics and application metrics

Status: implemented in Part 1 (2026-09-16).

## Problem

Application code emitted no lifecycle logs, request correlation or standard metrics. /stats omitted important outcomes.

## Production impact

Operators could not distinguish input failures, cache degradation, downstream outages or hangs.

## Root cause

Diagnostics were limited to generic server output and partial in-memory URL counters.

## Solution

Add JSON application events for startup/shutdown, completion, dependency failures and cleanup. Validate or generate request IDs and return them to callers. Log safe error types and rule IDs rather than raw exceptions, URLs, signatures or bodies. Add isolated per-process Prometheus registry and /metrics for requests, latency, cache outcomes, forwarding and in-flight work.

## Alternatives considered

Plain text is easy initially but harder to query consistently. OpenTelemetry could unify telemetry but adds an unnecessary pipeline at this stage. Prometheus client plus standard logging fits the requested future stack.

## How to verify the fix

Run metric population and JSON log redaction tests. GET /metrics after signed traffic. This change provides hooks only: dashboard, scraper, resource collectors and alerts belong to Part 4, not this fix.

