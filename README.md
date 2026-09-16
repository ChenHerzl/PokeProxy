# PokeProxy

A reverse proxy service that receives Pokemon data streams as protobuf-encoded payloads, validates HMAC signatures, matches against configurable routing rules, and forwards matching Pokemon as JSON to downstream services. It includes a Redis caching layer to avoid re-processing previously seen payloads.

## How It Works

```
[Client] --POST protobuf+HMAC--> [PokeProxy /stream]
                                       |
                                  1. Validate HMAC signature
                                  2. Check Redis cache
                                  3. Decode protobuf (on cache miss)
                                  4. Match against routing rules
                                  5. Convert to JSON
                                  6. Forward to downstream
                                       |
                                  [Downstream Service]
```

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Redis server running locally

## Quick Start

```bash
# Install dependencies
uv sync --frozen --dev

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Start Redis (if not already running)
redis-server &

# Start the mock downstream service
uv run uvicorn mock_service.main:app --host 127.0.0.1 --port 8001 &

# Start PokeProxy
uv run uvicorn pokeproxy.main:app --host 127.0.0.1 --port 8000
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POKEPROXY_SECRET` | Yes | - | Base64-encoded HMAC secret, at least 32 decoded bytes |
| `POKEPROXY_CONFIG` | Yes | - | Path to rules JSON file |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL |

### Rules Config

Rules are loaded from the JSON file specified by `POKEPROXY_CONFIG`.

```json
{
  "rules": [
    {
      "url": "http://localhost:8001/pokemon",
      "reason": "strong fire pokemon",
      "match": ["type_one==Fire", "attack>80", "generation<4"]
    }
  ]
}
```

**Match operators:** `==`, `!=`, `>`, `<`

**Match logic:** All conditions in a rule must match (AND). First matching rule wins.

**Fields:** `number`, `name`, `type_one`, `type_two`, `total`, `hit_points`, `attack`, `defense`, `special_attack`, `special_defense`, `speed`, `generation`, `legendary`

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stream` | POST | Proxy endpoint — validates, matches, forwards |
| `/health` | GET | Health check |
| `/stats` | GET | Per-endpoint metrics |

## Load Generator

A load generator script is included to send synthetic Pokemon traffic:

```bash
uv run python scripts/load_generator.py --rps 10 --duration 60
```

Options:
- `--url` — Target URL (default: `http://localhost:8000/stream`)
- `--rps` — Requests per second (default: 10)
- `--duration` — Duration in seconds, 0 for infinite (default: 60)
- `--secret` — Base64-encoded HMAC secret (default: test secret)

## Testing

```bash
uv run --frozen pytest -v
```

## Startup configuration and dependency verification

Run commands from the repository root. `POKEPROXY_SECRET` is the supported
secret variable; `POKEPROXY_HMAC_KEY` is not supported. The value must be strict
base64 encoding of at least 32 bytes. The public key in `.env.example` and the
load generator is only for local demonstrations. Generate a random key with:

```bash
python3 -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
```

Store it in `.env` (ignored by Git) and use the same key for clients. The length
check does not measure entropy. Settings representations and human-readable
validation errors hide secret values; do not log raw environment variables or
Pydantic `ValidationError.errors()` / `.json()`, which can include input values.
Set the listener port with Uvicorn's `--port`; `POKEPROXY_PORT` has been removed
because it never controlled the server.

Install using `uv sync --frozen --dev` and run `uv run --frozen pytest -v`.
The protobuf runtime minimum is 6.31.1 to match the generated code; the lock file
selects the exact normal development/runtime dependencies. Regenerate protobuf
files with `sh scripts/generate_proto.sh` (requires uv and package-index access).
The isolated generator is pinned to `grpcio-tools==1.74.0`; review generated diffs
and rerun tests after changing the schema.

See [step 1 verification](docs/verification/step-1.md) for results and scope,
[review findings](docs/issues/README.md) for remaining work, and
[planning decisions](docs/planning/part-1-review.md).
