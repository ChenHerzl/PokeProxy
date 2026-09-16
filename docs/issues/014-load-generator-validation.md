# Make load-generator results honest and failures actionable

Status: implemented in Part 1 (2026-09-16).

## Problem

Sequential requests slept a whole interval after each response, accepted invalid rates/durations, and exited successfully despite HTTP failures.

## Production impact

Actual request rate was lower than advertised and shell automation could mistake failed traffic for success.

## Root cause

Pacing ignored request time; input and process-exit semantics were not defined.

## Solution

Validate finite positive RPS/nonnegative duration and secret encoding. Pace using monotonic start times without unlimited catch-up, report achieved rate, and exit nonzero on errors. Read secret from POKEPROXY_SECRET before the public development fallback; retain --secret for compatibility and document argv exposure. Print exception type rather than possibly credential-bearing URL text.

## Alternatives considered

Bounded asynchronous load could reach higher rates but expands scope. A dedicated benchmark tool is appropriate later. Keep this script sequential and explicitly report its capacity limitation.

## How to verify the fix

Run test_load_generator_rejects_invalid_args and test_load_generator_pacing_and_error_exit. Use --duration 1 against a failing endpoint and expect a nonzero exit. This is traffic generation, not the release verifier.

