# Keep Redis failures from breaking forwarding

Status: implemented in Part 1 (2026-09-16).

## Problem

Redis errors, malformed cached JSON and schema-invalid entries escaped the request handler; a failed cache write stopped valid delivery.

## Production impact

An optimization became a mandatory dependency and corrupt entries caused repeated failures until expiry.

## Root cause

Cache reads/writes had no explicit failure policy or total operation budget.

## Solution

Use bounded Redis operations with no automatic retries. Fall back to decoding the signed original on Redis error, timeout or invalid cache data. A failed write does not prevent forwarding. Validate cache JSON strictly, emit redacted events/counters, and report the last cache operation health in readiness without failing readiness.

## Alternatives considered

Fail-closed caching is justified for an authoritative idempotency store, not this reconstructible data. Removing Redis would abandon assignment scope. A circuit breaker is deferred until measurements justify its extra state.

## How to verify the fix

Run corruption, read/write outage, timeout and recovery tests in tests/test_hardening.py. Real socket tests exercise proxy-to-mock flow with Redis available and unreachable. Readiness remains 200 with cache=degraded after failures.

