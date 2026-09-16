# Expose validated budgets and reject excess work

Status: implemented in Part 1 (2026-09-16).

## Problem

There was no application admission bound, response-size bound, total upload deadline or operational configuration for important limits.

## Production impact

High concurrency or a large/slow downstream could multiply memory, connection and waiting-task consumption.

## Root cause

Hardcoded client values and body checks did not define a coherent resource envelope.

## Solution

Add validated environment settings for HTTP/Redis/upload/cleanup budgets, TTL, body/response bytes and maximum in-flight requests. Reject excess /stream work immediately with 503, without an unbounded waiting queue. Bound HTTP/Redis pools consistently. Buffer only capped raw downstream bytes and always close responses.

## Alternatives considered

A semaphore with unlimited waiters bounds active calls but not backlog. Queueing changes delivery semantics. Server/ingress limits are useful defense in depth and will accompany deployment; application admission protects direct callers now.

## How to verify the fix

Run invalid-budget, admission/cancellation, response cap and slow-upload tests. Tune defaults against measured traffic; these are conservative starting values, not a demonstrated production capacity guarantee.

