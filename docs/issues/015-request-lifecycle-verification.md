# Verify the real request lifecycle and downstream receipt

Status: implemented in Part 1 (2026-09-16).

## Problem

Original basic/settings tests did not exercise /stream, cache failures, forwarding, shutdown or actual receipt content.

## Production impact

A passing test suite or HTTP 200 could hide broken routing and delivery; unmatched requests already return 200.

## Root cause

Tests focused on helpers and startup, without an end-to-end acceptance oracle.

## Solution

Add request-level regressions, real Redis expiry coverage and Uvicorn process/signal tests. Add scripts/verify.py: send signed known-matching Charizard, attach a unique request ID, query only its receipt, validate all JSON fields/reason/count, and fail on missing/wrong receipt or HTTP error. Never reset global receipts.

## Alternatives considered

Health-only verification cannot prove business flow. The random load generator cannot guarantee a matching request or validate receipts. A deterministic correlated verifier is small and reusable by future deployment automation.

## How to verify the fix

Run the complete suite with TEST_REDIS_URL. Run uv run --frozen python scripts/verify.py against running proxy/mock with POKEPROXY_SECRET exported. Real tests also show a no-match HTTP 200 fails receipt verification.

