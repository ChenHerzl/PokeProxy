from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from http.cookiejar import CookieJar

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from pokeproxy.cache import PokemonCache
from pokeproxy.config import Settings
from pokeproxy.http import RejectCookies
from pokeproxy.logging import configure_logging, event
from pokeproxy.proxy import router as proxy_router
from pokeproxy.rules import load_rules
from pokeproxy.stats import StatsRegistry


async def close_resource(client, budget: float, resource: str) -> None:
    try:
        async with asyncio.timeout(budget):
            await client.aclose()
    except Exception as exc:
        event("cleanup_failed", resource=resource, error_type=type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.ready = False
    settings = Settings()
    rules = load_rules(settings.pokeproxy_config)
    app.state.settings = settings
    app.state.rules = tuple(rules)
    app.state.hmac_key = settings.hmac_key
    app.state.stats = StatsRegistry()
    app.state.active = 0
    async with AsyncExitStack() as stack:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.pokeproxy_http_timeout),
            limits=httpx.Limits(
                max_connections=settings.pokeproxy_max_inflight,
                max_keepalive_connections=settings.pokeproxy_max_inflight,
            ),
            trust_env=False,
            cookies=CookieJar(policy=RejectCookies()),
        )
        stack.push_async_callback(
            close_resource, client, settings.pokeproxy_close_timeout, "http"
        )
        app.state.http_client = client
        redis = aioredis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=settings.pokeproxy_redis_timeout,
            socket_connect_timeout=settings.pokeproxy_redis_timeout,
            max_connections=settings.pokeproxy_max_inflight,
            retry=Retry(NoBackoff(), 0),
        )
        stack.push_async_callback(
            close_resource, redis, settings.pokeproxy_close_timeout, "redis"
        )
        app.state.cache = PokemonCache(redis, settings, app.state.stats)
        app.state.ready = True
        event("startup_complete", rules=len(rules))
        try:
            yield
        finally:
            app.state.ready = False
            event("shutdown_started", active_requests=app.state.active)
    event("shutdown_complete")


app = FastAPI(title="PokeProxy", lifespan=lifespan)
app.include_router(proxy_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/ready")
async def ready(request: Request) -> Response:
    state = request.app.state
    if not getattr(state, "ready", False):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    cache = {True: "healthy", False: "degraded", None: "unknown"}[state.cache.healthy]
    return JSONResponse(
        {"status": "ready", "cache": cache, "cache_observation": "last_operation"}
    )


@app.get("/stats")
async def stats(request: Request) -> dict:
    return request.app.state.stats.to_dict()


@app.get("/metrics")
async def metrics(request: Request) -> Response:
    return Response(
        generate_latest(request.app.state.stats.registry),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
