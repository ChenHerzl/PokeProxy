# Replace lifetime latency samples with bounded histograms

Status: implemented in Part 1 (2026-09-16).

## Problem

Every completed forward inserted latency into a sorted list that never expired and was not exposed by /stats.

## Production impact

Memory and list-insertion work grew for the process lifetime.

## Root cause

Exact lifetime percentiles were implemented by retaining every observation.

## Solution

Remove the lifetime list. Export fixed Prometheus histogram buckets for inbound and downstream durations; retain only cumulative count/sum fields in the small compatibility summary. Labels use startup rule indices, not payloads or arbitrary URLs.

## Alternatives considered

A bounded rolling sample can offer local percentiles but is unnecessary for Prometheus. Exact lifetime percentiles require growing storage. Fixed histograms support aggregation across pods with predictable memory.

## How to verify the fix

Run test_bounded_metrics_storage (10,000 observations) and metric exposition tests. No _response_times list remains; bucket/sample count stays fixed.

