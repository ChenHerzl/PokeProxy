# Make traffic and error accounting consistent

Status: implemented in Part 1 (2026-09-16).

## Problem

bytes_received was overwritten; only HTTP responses incremented requests while transport errors incremented errors and elapsed totals. bytes_sent ignored retry semantics.

## Production impact

Reported throughput and average latency were incorrect; error rates could exceed one or report zero during complete failure.

## Root cause

Counters described different lifecycle populations and assignments were used instead of accumulation.

## Solution

Count every admitted downstream attempt once before sending and every non-success outcome once. Accumulate received bytes and attempted JSON bytes; measure matching duration population. Count every /stream completion separately, including rejection, no-match and cancellation. Expose /stats by bounded rule index to avoid raw URL disclosure.

## Alternatives considered

Keeping per-URL output is compatible but leaks URL details and merges rules. Only inbound counters would hide downstream causes. Separate inbound and forwarding populations explain both.

## How to verify the fix

Run mixed status/transport failure, cumulative byte and metrics tests. /stats error_rate stays within [0,1]; bytes_sent explicitly means attempted payload bytes, not confirmed socket delivery.

