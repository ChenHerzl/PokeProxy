# Bound downstream requests and reuse the HTTP client

Status: implemented in Part 1 (2026-09-16).

## Problem

The forwarding loop retried connection errors and timeouts forever, constructed a client on every attempt, and never closed those clients (`proxy.py`).

## Production impact

An outage accumulated requests, sockets and memory. A timeout after downstream acceptance could cause duplicate POST delivery.

## Root cause

The route ignored the lifespan-managed client and had no retry or overall time budget.

## Solution

Forward through the shared HTTPX client with bounded connections, configurable phase timeout and total downstream deadline. Make one attempt. Return contextual 502/504 JSON with request ID. Preserve downstream HTTP statuses and cancellation. Close streaming responses even on size rejection.

## Alternatives considered

Bounded connection-only retries might improve transient failure recovery but require precise pre-send guarantees. Idempotency-aware retries need a downstream contract absent here. One attempt is the defensible default.

## How to verify the fix

Run tests/test_hardening.py: single-attempt connect/read failures, total deadline, raw response closure and cancellation. tests/test_runtime.py verifies a real blocked downstream returns 504 and the process drains under both termination signals.

