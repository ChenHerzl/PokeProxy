# Validate routing configuration before serving traffic

Status: implemented in Part 1 (2026-09-16).

## Problem

Rules were synchronously loaded and parsed during every authenticated request. Missing rules silently became an empty list; URL and header-value validation was weak.

## Production impact

Invalid configuration appeared as traffic-time failures or silent no-op routing. Filesystem work blocked the event loop on every request.

## Root cause

Lifespan stored only the filename; loading accepted loosely shaped JSON and coerced values to strings.

## Solution

Load and validate once before allocating clients or marking ready. Require the documented schema, valid HTTP(S) host without credentials/fragment, printable reason at most 1024 characters, and a nonempty list of string conditions per rule. Keep rules in a startup tuple. Explicit rules=[] remains allowed; missing rules is an error. Error messages identify the rule index without exposing raw configuration.

## Alternatives considered

Atomic live reload could preserve a last-known-good ruleset but adds concurrency/versioning. A remote config service is excessive here. Restart-based changes provide a simple auditable snapshot.

## How to verify the fix

Run invalid-config, missing-file startup, first-match and operator tests. The full-flow test removes the rules file after startup and verifies that requests still work from memory.

