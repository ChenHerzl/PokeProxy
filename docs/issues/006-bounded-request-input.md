# Bound request reads and validate HTTP input

Status: implemented in Part 1 (2026-09-16).

## Problem

The entire body was buffered before its actual size check. Content-Length parsing could raise, and non-ASCII signatures could crash constant-time comparison.

## Production impact

Oversized or slow uploads consumed unbounded request resources; malformed headers could trigger 500 responses.

## Root cause

The application trusted a header as an early size signal but had no bounded streaming read or signature-format validation.

## Solution

Read chunks under a configurable upload deadline and stop before accumulating more than the size limit. Reject malformed, duplicate or mismatched Content-Length. Validate a single lowercase SHA-256 hexadecimal signature before compare_digest; use controlled 400/401/408/413 responses. Keep authentication before cache access.

## Alternatives considered

An ingress-only limit leaves other callers unprotected and is not available in Part 1. Buffer-then-check is too late. A bounded incremental buffer keeps protobuf decoding simple.

## How to verify the fix

Run body early-stop, malformed length, slow upload, duplicate signature, non-ASCII signature and invalid protobuf tests. Existing valid HMAC and unmatched routing behavior remain covered.

