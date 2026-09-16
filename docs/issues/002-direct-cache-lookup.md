# Use direct Redis cache lookup

Status: implemented in Part 1 (2026-09-16).

## Problem

Each read enumerated every pokeproxy key with KEYS before GET.

## Production impact

Latency and Redis blocking grew with keyspace size despite the caller already knowing the exact key.

## Root cause

A database enumeration was used for existence checking instead of GET semantics.

## Solution

PokemonCache.get issues one GET for the body-derived key. A missing key is a miss. TTL remains 300 seconds by default, now configurable; hits do not renew TTL.

## Alternatives considered

SCAN avoids a single blocking enumeration but remains unnecessary work. EXISTS followed by GET adds a round trip and race. GET alone answers both existence and content.

## How to verify the fix

The request-flow test supplies a Redis double exposing only get/set/aclose, proving no enumeration API is required. TEST_REDIS_URL enables a real hit/miss/TTL-expiry test in tests/test_runtime.py.

