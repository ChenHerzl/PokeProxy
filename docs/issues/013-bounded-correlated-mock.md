# Bound mock receipts and isolate verification runs

Status: implemented in Part 1 (2026-09-16).

## Problem

The mock stored every body forever, returned the entire list, accepted arbitrary JSON, and relied on global DELETE between checks.

## Production impact

Traffic exhausted memory; simultaneous checks could erase each other; multiple replicas had inconsistent receipt views.

## Root cause

The mock had an unlimited process-global list with no correlation or input contract.

## Solution

Create a configurable bounded deque in lifespan, bound input size/time, validate Pokemon JSON, retain reason and request ID, and support filtered GET /received?request_id=. Add a cheap health endpoint. Preserve DELETE for manual local reset but never use it in the verifier. Use one worker/replica and document ephemeral receipts.

## Alternatives considered

A shared Redis receipt store supports replicas but couples the verification oracle to cache health. A database is excessive for this test service. Bounded single-process storage is enough to prove delivery.

## How to verify the fix

Run test_mock_retention_correlation_and_validation and real signed receipt tests. Old receipts are evicted; lookups do not clear other runs. Administrative endpoints still require private deployment networking in Part 2.

