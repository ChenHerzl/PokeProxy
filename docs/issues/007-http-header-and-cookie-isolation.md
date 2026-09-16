# Preserve HTTP framing and isolate request metadata

Status: implemented in Part 1 (2026-09-16).

## Problem

The proxy copied compressed response headers onto HTTPX-decoded bytes; request filtering omitted hop-by-hop fields and allowed duplicate routing-reason casing.

## Production impact

Clients could receive incorrectly framed responses, and downstreams could receive misleading encoding metadata or spoofed routing headers.

## Root cause

Header copying did not account for body transformation, Connection-nominated fields, duplicate headers, or shared-client cookie state.

## Solution

Read bounded raw downstream bytes and preserve their encoding; regenerate Content-Length. Filter hop-by-hop and Connection-nominated fields in both directions. Remove stale request body metadata and credentials; replace proxy-owned reason/request ID case-insensitively. Preserve repeated response fields through raw_headers. Disable HTTPX cookie retention so pooled clients do not carry session cookies across callers.

## Alternatives considered

Decoded buffering would require stripping encoding and enforcing decompressed limits. Full streaming adds lifecycle/error complexity. Raw bounded buffering preserves bytes and existing response semantics without decompression inside the proxy.

## How to verify the fix

Run gzip framing/header filtering, spoofed reason and shared-client cookie tests. Verify the downstream response stream closes on rejection and that raw compressed size is enforced. Clients remain responsible for safe decompression of encoded downstream content.

