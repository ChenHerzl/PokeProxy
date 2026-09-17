# Production issues

Current Part 1 implementation records are below. Each file describes the original
problem, impact, cause, implemented solution, alternatives and reproducible checks.
See [hardening plan/results](../planning/02-production-hardening.md) for the tested
scope and remaining risks. The [initial assessment](../planning/01-initial-assessment.md)
preserves the pre-hardening findings; it is not the current implementation status.
The full [original review](../planning/00-original-code-review.md) is also retained
so earlier evidence and limitations are not lost.

| ID | Implemented fix |
| --- | --- |
| 001 | [Bound downstream requests and reuse the HTTP client](001-bounded-downstream-requests.md) |
| 002 | [Use direct Redis cache lookup](002-direct-cache-lookup.md) |
| 003 | [Keep Redis failures from breaking forwarding](003-best-effort-cache.md) |
| 005 | [Validate routing configuration before serving traffic](005-startup-routing-validation.md) |
| 006 | [Bound request reads and validate HTTP input](006-bounded-request-input.md) |
| 007 | [Preserve HTTP framing and isolate request metadata](007-http-header-and-cookie-isolation.md) |
| 008 | [Replace lifetime latency samples with bounded histograms](008-bounded-latency-metrics.md) |
| 009 | [Make traffic and error accounting consistent](009-correct-request-accounting.md) |
| 010 | [Add structured diagnostics and application metrics](010-structured-observability.md) |
| 011 | [Close resources reliably and verify graceful termination](011-graceful-resource-lifecycle.md) |
| 013 | [Bound mock receipts and isolate verification runs](013-bounded-correlated-mock.md) |
| 014 | [Make load-generator results honest and failures actionable](014-load-generator-validation.md) |
| 015 | [Verify the real request lifecycle and downstream receipt](015-request-lifecycle-verification.md) |
| 016 | [Expose validated budgets and reject excess work](016-resource-budgets.md) |
| 017 | [Keep credentials out of settings and diagnostics](017-secret-safe-configuration.md) |

## Previously resolved

004 (HMAC configuration) and 012 (protobuf minimum) were resolved before this
hardening pass. Evidence remains in [step 1 verification](../verification/step-1.md)
and [earlier planning](../planning/part-1-review.md). They were not redone here.

## Remaining assessment items

- A01 is covered by 007/016; A02 by 013; application-level A06 by 017.
- A03: replay acceptance and trusted cache writers remain explicit contracts;
  no delivery deduplication or cryptographic cache-integrity layer was introduced.
- A04: containers, orchestration, mock image packaging and delivery automation
  were subsequently implemented in [infrastructure](../planning/03-local-deployment.md),
  [CI/CD](../planning/04-cicd-gitops.md) and [automation](../planning/06-automation.md).
- A05: this index, README and current hardening plan supersede earlier ELK and
  pre-fix status text. Historical documents are retained with a status notice.
- Scraping, process resource metrics, Grafana and alert rules were subsequently
  implemented in [observability](../planning/05-observability.md); local secret
  provisioning is covered by automation. Deployment TLS, enforced network policy,
  full cluster resource metrics and external alert delivery remain limitations.
