from __future__ import annotations

import asyncio
import hashlib
import hmac
import random
import time

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from pokeproxy.cache import cache_pokemon, get_cached_pokemon, make_cache_key
from pokeproxy.config import PokemonJSON, Rule, decode_pokemon
from pokeproxy.rules import load_rules, match_pokemon
from pokeproxy.stats import StatsRegistry

router = APIRouter()

MAX_BODY_SIZE = 1_048_576  # 1 MiB

STRIP_HEADERS = frozenset({
    "x-grd-signature",
    "content-type",
    "content-length",
    "host",
    "transfer-encoding",
    "authorization",
    "cookie",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
})


def verify_signature(secret: bytes, body: bytes, signature: str) -> bool:
    expected = hmac.new(key=secret, msg=body, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _build_forward_headers(
    original_headers: dict[str, str], reason: str
) -> dict[str, str]:
    headers: dict[str, str] = {
        k: v
        for k, v in original_headers.items()
        if k.lower() not in STRIP_HEADERS
    }
    headers["Content-Type"] = "application/json"
    headers["X-Grd-Reason"] = reason
    return headers


async def _forward_with_retry(
    url: str,
    content: bytes,
    headers: dict[str, str],
) -> httpx.Response:
    delay = 0.1
    while True:
        try:
            client = httpx.AsyncClient(timeout=600.0)
            return await client.post(url, content=content, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError):
            delay = min(delay * 2 * (0.5 + random.random()), 30.0)  # noqa: S311
            await asyncio.sleep(delay)


async def _forward_request(
    rule: Rule,
    pokemon: PokemonJSON,
    original_headers: dict[str, str],
    stats: StatsRegistry,
) -> Response:
    json_bytes = pokemon.model_dump_json().encode()
    headers = _build_forward_headers(original_headers, rule.reason)

    endpoint_stats = stats.get(rule.url)
    start = time.monotonic()

    try:
        resp = await _forward_with_retry(rule.url, json_bytes, headers)

        if resp.status_code >= 400:
            endpoint_stats.error_count += 1

        endpoint_stats.request_count += 1

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    except httpx.TimeoutException:
        endpoint_stats.error_count += 1
        return JSONResponse(
            content={"error": "downstream timeout"},
            status_code=504,
        )
    except httpx.HTTPError:
        endpoint_stats.error_count += 1
        return JSONResponse(
            content={"error": "downstream error"},
            status_code=502,
        )
    finally:
        elapsed = time.monotonic() - start
        endpoint_stats.record_response_time(elapsed)
        endpoint_stats.bytes_sent += len(json_bytes)


@router.post("/stream")
async def stream(request: Request) -> Response:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        return JSONResponse(
            content={"error": "payload too large"},
            status_code=413,
        )

    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        return JSONResponse(
            content={"error": "payload too large"},
            status_code=413,
        )

    secret: bytes = request.app.state.hmac_key
    stats: StatsRegistry = request.app.state.stats
    redis_client = request.app.state.redis

    signature = request.headers.get("X-Grd-Signature", "")
    if not signature or not verify_signature(secret, body, signature):
        return JSONResponse(
            content={"error": "invalid signature"},
            status_code=401,
        )

    body_hash = hashlib.sha256(body).hexdigest()
    cache_key = make_cache_key(body_hash)

    cached = await get_cached_pokemon(redis_client, cache_key)
    if cached is not None:
        pokemon = PokemonJSON(**cached)
    else:
        try:
            pokemon = decode_pokemon(body)
        except ValueError:
            return JSONResponse(
                content={"error": "invalid protobuf"},
                status_code=400,
            )
        await cache_pokemon(redis_client, cache_key, pokemon)

    rules = load_rules(request.app.state.config_path)
    matched_rule = match_pokemon(pokemon, rules)

    if matched_rule is None:
        return JSONResponse(content={}, status_code=200)

    endpoint_stats = stats.get(matched_rule.url)
    endpoint_stats.bytes_received = len(body)

    original_headers: dict[str, str] = dict(request.headers)

    return await _forward_request(
        matched_rule, pokemon, original_headers, stats
    )
