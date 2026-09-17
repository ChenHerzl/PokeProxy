# Managed local tunnel verification

Tested on 2026-09-17 against the existing running kind stack. No cluster teardown
or fresh deployment was performed for this change. All processes bind 127.0.0.1.

| Command | Exact observed result |
| --- | --- |
| `make tunnels` | Exited 0; proxy PID 205248, Prometheus PID 205284, Grafana PID 205321; all readiness checks passed |
| `make tunnels` again | Exited 0; reported already running with the same three PIDs; no duplicates |
| `curl -fsS http://127.0.0.1:8000/ready` | `{"status":"ready","cache":"healthy","cache_observation":"last_operation"}` |
| `curl -fsS http://127.0.0.1:9090/-/ready` | `Prometheus Server is Ready.` |
| `curl -fsS -o /dev/null -w 'Grafana HTTP %{http_code}\n' http://127.0.0.1:3000/d/pokeproxy-health` | `Grafana HTTP 200` |
| `make tunnels-down` | Exited 0; removed all three PID records |
| `make tunnels-down` again | Exited 0 with no managed processes remaining |
| TCP connection checks to 8000, 9090 and 3000 after shutdown | All connections failed: ports closed |
| `.local/tunnels/*.pid` check | No PID records remain; individual logs retained |

The manager uses a checkout-local lock, detached processes with separate logs,
PID/start-time/command identity checks, and Linux pidfds for targeted termination.
It verifies HTTP 200 on application readiness, Prometheus readiness and Grafana
health before declaring success. Startup failure removes newly started tunnels;
healthy pre-existing managed tunnels are retained. Unrelated port owners cause
an actionable error and are not killed. Unexpected exits/stale PID records are
handled on the next command; this is not a continuously running supervisor.

Regression tests cover healthy-process reuse, stale PID identity protection and
missing/corrupt record cleanup. The three focused tests passed. Managed tunnels
were left stopped after testing; the Kubernetes stack was left running.
