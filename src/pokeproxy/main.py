from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request

from pokeproxy.config import Settings
from pokeproxy.proxy import router as proxy_router
from pokeproxy.stats import StatsRegistry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = Settings()  # type: ignore[call-arg]

    app.state.config_path = settings.pokeproxy_config
    app.state.hmac_key = settings.hmac_key
    app.state.stats = StatsRegistry()

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
    )
    app.state.http_client = http_client

    redis_client = aioredis.from_url(settings.redis_url)
    app.state.redis = redis_client

    yield

    await redis_client.aclose()
    await http_client.aclose()


app = FastAPI(title="PokeProxy", lifespan=lifespan)
app.include_router(proxy_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/stats")
async def stats(request: Request) -> dict[str, Any]:
    stats_registry: StatsRegistry = request.app.state.stats
    return stats_registry.to_dict()
