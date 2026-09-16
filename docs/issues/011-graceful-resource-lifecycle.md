# Close resources reliably and verify graceful termination

Status: implemented in Part 1 (2026-09-16).

## Problem

Cleanup after lifespan yield was not protected; initialization or Redis close failure could skip HTTP cleanup. There was no readiness endpoint.

## Production impact

Partial startup leaked resources; deployments could appear healthy despite invalid routing; unbounded requests obstructed shutdown.

## Root cause

Resources were acquired without independent cleanup registration and liveness was treated as the only health signal.

## Solution

Use AsyncExitStack with separately registered, bounded client cleanup; validate settings/rules first. Separate /health liveness from /ready initialized/draining state. Redis last-operation degradation is informative, not a readiness dependency. Leave SIGTERM/SIGINT ownership with Uvicorn; bounded request work drains before lifespan closes resources. Document a finite graceful-shutdown server setting.

## Alternatives considered

Custom signal handlers would compete with Uvicorn and are unnecessary. Restarting on Redis/downstream outage would amplify external failures. Simple nested finally blocks are valid but harder to extend safely than an exit stack.

## How to verify the fix

Run partial-startup and close-failure tests, readiness tests, cancellation checks and real Uvicorn SIGTERM/SIGINT tests under a hanging downstream. Both signals finish the request with 504 then emit shutdown_complete.

