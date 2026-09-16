# Keep credentials out of settings and diagnostics

Status: implemented in Part 1 (2026-09-16).

## Problem

Redis URLs were plain strings and could contain passwords. Future dotenv variants were not ignored. Error/traffic-tool output could expose URL or CLI secrets.

## Production impact

Settings dumps, exception logs and accidental commits could disclose credentials.

## Root cause

Only the HMAC field had secret-aware storage, and ignore rules covered just .env.

## Solution

Store Redis URL as SecretStr, validate rediss/redis host/database syntax and forbid query options that override operational budgets. Emit sanitized error types and rule indices; reject credentials in routing URLs. Ignore .env.* except the example and .secrets/. Prefer environment secrets in tools. Preserve the existing strict HMAC validation and public development example.

## Alternatives considered

An external secret manager fits production infrastructure but is outside application hardening. Removing CLI compatibility would break existing usage; documented environment input is safer while retaining the flag. Never claim base64 is encryption.

## How to verify the fix

Run Redis URL redaction and structured-log tests, inspect git diff for literal deployment secrets, and use generated credentials outside Git. Secret rotation, Redis ACL/TLS deployment and private admin endpoint access remain operational responsibilities.

