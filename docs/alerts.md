# PokeProxy alerts and operator actions

Definitions live in [alerts.yml](../deploy/monitoring/prometheus/alerts.yml).
Prometheus evaluates them every 15 seconds. View pending/firing state at
`http://127.0.0.1:9090/alerts` after starting the documented port-forward.
Alertmanager and external notification delivery are intentionally not installed.
Severity describes intended urgency, not an existing pager integration.

Thresholds are initial local demonstration settings, not measured production
SLOs. Four ratio alerts require at least 20 observations in their five-minute
lookback, preventing one event from appearing as a sustained 100% failure rate.
This deliberately sacrifices alert sensitivity at very low traffic; the E2E
deployment gate and dashboard remain useful there. Rate/increase calculations
handle counter resets, and aggregation occurs after calculating per-series
increases. Sustained conditions must hold throughout the stated `for` duration.

## PokeProxyScrapeUnavailable

- **Condition/threshold:** the sum of `up{job="pokeproxy"}` is zero, including
  when discovery returns no series at all.
- **Window:** current scrape state, held for **1 minute**. Scrapes run every 15s.
- **Why:** a single missed scrape or short rolling transition should not alert;
  a minute with no reachable proxy metrics warrants investigation. This measures
  scrape availability, not a successful business transaction or Pod readiness.
- **Action:** inspect Prometheus Targets and logs, then proxy Pods, startup logs,
  scheduling/image errors, ServiceAccount RBAC and Pod connectivity. Restore the
  deployment or discovery access; run the E2E gate after recovery. If Prometheus
  itself is down it cannot evaluate this alert; production needs an independent
  monitor/watchdog. If another replica remains scrapeable this service-level
  alert does not detect reduced replica count.

## PokeProxyForwardingFailures

- **Condition/threshold:** over **5%** of finished downstream attempts have an
  outcome other than `success`, with **at least 20 attempts** in the last **5m**.
  The condition must persist for **2m**. Severity: **critical**.
- **Why:** this is application-specific: only matched Pokémon cause forwarding.
  HTTP errors, transport errors, timeouts, oversized responses, cancellation and
  internal forwarding failures all count. Downstream HTTP 4xx counts here even
  though it is not a proxy HTTP 5xx. An invalid incoming HMAC never reaches this
  population. A sustained 5% loss of expected forwarding is material while a
  single failed attempt should not trigger an incident.
- **Action:** compare forwarding outcomes and latency, correlate safe request IDs
  in proxy/mock logs, and check downstream readiness/connectivity and routing
  configuration. Revert an implicated release using [the rollback procedure](rollback.md).
  Do not blindly replay POSTs: a timeout can occur after downstream acceptance.

## PokeProxyServerErrors

- **Condition/threshold:** over **5%** of finished `/stream` handlers report HTTP
  **5xx**, with **at least 20 completions** in **5m**, held for **2m**.
  Severity: **critical**.
- **Why:** catches admission rejection, internal errors and downstream 502/504
  responses. It complements the forwarding alert because a request can fail
  before any downstream attempt. Invalid HMAC (401), invalid protobuf (400),
  oversized upload (413), and valid unmatched HTTP 200 are not server errors.
  Cancellation is recorded as 503 even if no response can reach the caller.
- **Action:** inspect handler outcomes and in-flight gauge. Distinguish capacity
  rejection from dependency failure; inspect logs and CPU/memory before changing
  limits. Correlate with the forwarding alert and rollback recent bad changes.
  These alerts may overlap; a future Alertmanager route should group by service.

## PokeProxySlowProcessing

- **Condition/threshold:** over **10%** of finished handlers take more than
  **1 second**, with **at least 20 completions** in **5m**, held for **5m**.
  Severity: **warning**. The ratio uses histogram count minus the `le="1.0"`
  bucket, not an average or averaged per-Pod percentiles.
- **Why:** the local proxy/mock path should normally finish far below one second.
  A sustained tail above that level indicates degradation before the ten-second
  downstream deadline is exhausted. The five-minute hold tolerates transient
  startup/resource contention. This is a tuning starting point, not a user SLO.
- **Action:** compare request and downstream histograms, cache errors, CPU and
  in-flight work. A slow upload is included in handler time; response transmission
  to the caller is not. Check upload behavior when only handler latency grows;
  inspect the downstream when both grow. Adjust thresholds only with measured
  workload evidence, not simply to silence an alert.

## PokeProxyCacheErrors

- **Condition/threshold:** `read_error` plus `write_error` exceeds **10%** of all
  recorded cache operations, with **at least 20 operations** in **5m**, held for
  **5m**. Severity: **warning**.
- **Why:** Redis is a best-effort decode cache. Persistent errors lose the
  optimization and consume timeout budget, but can coexist with successful
  forwarding. A warning with a longer hold reflects degradation rather than
  assuming total application outage. Corrupt cached JSON is a read error too.
- **Action:** inspect Redis readiness, credentials, DNS/network connectivity and
  safe `cache_degraded` logs. Check memory/eviction settings and malformed cache
  writers. Restore the cache without restarting healthy proxy Pods needlessly;
  confirm errors stop under real traffic. Readiness's last-operation cache state
  is not a continuous Redis probe and is not used as this alert's source.

## Deliberately not alerted on

- **Every invalid signature:** missing/malformed/incorrect signatures are correctly
  rejected before cache access or forwarding. Individual failures are caller
  errors, and alerting on each would be noisy and easy to trigger. The dashboard
  displays their rate; a sustained security/abuse policy needs real traffic data.
- **Unmatched Pokémon:** no-match HTTP 200 `{}` is valid routing behavior. A
  universal “no forwards” threshold would confuse legitimate traffic with a
  broken rule. The fixed matching E2E payload detects that deployment regression.
- **Cache misses or low hit ratio:** cold starts, TTL expiry and new payloads make
  misses normal. They are not Redis failures. Hit ratio excludes read errors and
  writes; the separate error signal explains degradation.
- **One Pod disappearing during a rollout, process RSS alone, or idle traffic:**
  these do not establish an actionable outage for this local service. Process
  RSS is not cgroup usage; no claim of Kubernetes OOM/throttling coverage is made.

## Rule verification

`bash scripts/validate-monitoring.sh` uses the same pinned Prometheus image as
the deployment to check configuration/rules and run nine `promtool test rules`
scenarios. They cover missing/unreachable targets, each failure threshold and
hold time, forwarding recovery, low-volume suppression, normal rejection/miss
traffic, and idle healthy operation. CI runs these checks too. The live monitoring
verifier also checks that all five rules are loaded and evaluate without errors.
