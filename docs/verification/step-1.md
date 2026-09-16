# Step 1 — startup and dependency baseline

Completed: 2026-09-16. Scope: findings 004 and 012, plus the test baseline.

## Deliverables

- [x] Documented secret variable works; strict base64 and 32-byte minimum enforced.
- [x] Secret-aware settings and redacted human-readable validation errors.
- [x] Development example and load-generator default agree.
- [x] Removed misleading port setting; documented Uvicorn configuration.
- [x] Corrected protobuf runtime minimum and refreshed dependency lock.
- [x] Pinned, reproducible protobuf generation command.
- [x] Ten configuration/startup cases added alongside five existing tests.
- [x] Locked and minimum-runtime suites pass; repository lint passes.
- [x] README, issue resolutions, decisions, and AI workflow updated.

## Verification performed

Python 3.13.5; uv 0.10.9 installed in `/tmp/pokeproxy-uv` for this session.
The host initially had no uv. Downloads required sandbox network approval.
Use your installed `uv` for the commands below.

| Check | Result |
| --- | --- |
| `uv lock` then `uv sync --frozen --dev` | Passed; protobuf remains 7.34.0 |
| `.venv/bin/pytest -q` | 15 passed in 0.28s |
| `.venv/bin/ruff check .` | All checks passed |
| Isolated protobuf 6.31.1 suite (command below) | 15 passed in 0.72s |
| grpcio-tools 1.74.0 `--version` | libprotoc 31.1 |
| Generate both files into a temporary directory and diff | Exact match for `.py` and `.pyi` |

Minimum-runtime command (does not alter `.venv` or `uv.lock`):

```bash
uv run --isolated --no-project \
  --with protobuf==6.31.1 --with pytest==9.0.2 \
  --with pytest-asyncio==1.3.0 --with . python -m pytest -q
```

This separate environment resolves other dependencies independently; it proves
compatibility with the protobuf minimum, not a minimum-version matrix for every
package. It emitted two upstream Starlette/AnyIO deprecation warnings. The locked
suite emitted none. The sandboxed locked run stalled in the threaded TestClient;
rerunning outside the sandbox completed successfully.

## Scope and remaining work

The startup smoke test exercises the actual FastAPI lifespan and `/health`
using `.env.example`. Redis connections are lazy: this does not demonstrate live
Redis availability, forwarding, rule validation, readiness, or deployment.
Those issues remain open. No container, cluster, CI/CD, or ELK deliverable is
claimed complete by this step.

Human-readable Pydantic errors redact inputs. Structured validation error APIs
can retain raw input and must not be logged. The public sample key is not a
production credential. A minimum length cannot establish entropy.

Git initialization was confirmed. Files remain uncommitted; no remote or
publication was configured. Review findings retain historical evidence and
proposed acceptance criteria, with explicit resolution notes for this step.
