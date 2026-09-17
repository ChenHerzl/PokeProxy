from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from pokeproxy.cache import make_cache_key
from pokeproxy.config import decode_pokemon
from pokeproxy.http import InputError, read_body, request_id
from pokeproxy.logging import event
from pokeproxy.rules import match_pokemon

router = APIRouter()

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
REQUEST_STRIP = HOP_HEADERS | {
    "x-grd-signature",
    "content-type",
    "content-length",
    "content-encoding",
    "content-md5",
    "digest",
    "host",
    "authorization",
    "cookie",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "forwarded",
    "x-grd-reason",
    "x-request-id",
}


def verify_signature(secret: bytes, body: bytes, signature: str) -> bool:
    if re.fullmatch(r"[0-9a-f]{64}", signature) is None:
        return False
    expected = hmac.new(key=secret, msg=body, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def filter_headers(headers: httpx.Headers, excluded: set[str]) -> list[tuple[str, str]]:
    nominated = {h.strip().lower() for h in headers.get("connection", "").split(",")}
    return [
        (k, v)
        for k, v in headers.multi_items()
        if k.lower() not in excluded | nominated
    ]


async def forward(
    request: Request, rule, pokemon, body_size: int, correlation: str
) -> Response:
    state = request.app.state
    settings, stats = state.settings, state.stats
    rule_id = f"rule_{state.rules.index(rule)}"
    content = pokemon.model_dump_json().encode()
    headers = filter_headers(httpx.Headers(request.headers.raw), REQUEST_STRIP)
    headers.extend(
        [
            ("content-type", "application/json"),
            ("x-grd-reason", rule.reason),
            ("x-request-id", correlation),
        ]
    )
    counters = stats.get(rule_id)
    counters.request_count += 1
    counters.bytes_received += body_size
    counters.bytes_sent += len(
        content
    )  # Attempted JSON bytes, not delivery confirmation.
    start = time.monotonic()
    outcome = "internal_error"
    try:
        async with asyncio.timeout(settings.pokeproxy_downstream_deadline):
            # Raw bytes preserve Content-Encoding and avoid decompressing untrusted responses.
            async with state.http_client.stream(
                "POST", rule.url, content=content, headers=headers
            ) as resp:
                response_body = bytearray()
                async for chunk in resp.aiter_raw():
                    if (
                        len(response_body) + len(chunk)
                        > settings.pokeproxy_max_response_bytes
                    ):
                        raise InputError(502, "downstream response too large")
                    response_body.extend(chunk)
                response = Response(bytes(response_body), status_code=resp.status_code)
                retained = filter_headers(
                    resp.headers, HOP_HEADERS | {"content-length", "x-request-id"}
                )
                response.raw_headers.extend(
                    (k.encode("latin-1"), v.encode("latin-1")) for k, v in retained
                )
                outcome = "http_error" if resp.status_code >= 400 else "success"
                return response
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except (TimeoutError, httpx.TimeoutException):
        outcome = "timeout"
        raise InputError(504, "downstream deadline exceeded") from None
    except httpx.HTTPError as exc:
        outcome = "transport_error"
        event(
            "downstream_failure",
            rule=rule_id,
            request_id=correlation,
            error_type=type(exc).__name__,
        )
        raise InputError(502, "downstream transport failure") from None
    except InputError:
        outcome = "response_too_large"
        raise
    finally:
        elapsed = time.monotonic() - start
        counters.total_response_time += elapsed
        if outcome != "success":
            counters.error_count += 1
        stats.forward.labels(rule_id, outcome).inc()
        stats.forward_duration.labels(rule_id).observe(elapsed)
        event(
            "forward_complete",
            rule=rule_id,
            request_id=correlation,
            outcome=outcome,
            duration_seconds=elapsed,
        )


@router.post("/stream")
async def stream(request: Request) -> Response:
    state = request.app.state
    correlation = request_id(request)
    start = time.monotonic()
    stats = state.stats
    stats.received.inc()
    status, outcome, admitted = 500, "internal_error", False
    try:
        if not state.ready:
            raise InputError(503, "service draining")
        # Single event-loop worker: check/increment has no suspension point.
        if state.active >= state.settings.pokeproxy_max_inflight:
            raise InputError(503, "request capacity exceeded")
        state.active += 1
        stats.inflight.inc()
        admitted = True
        body = await read_body(
            request,
            state.settings.pokeproxy_max_body_bytes,
            state.settings.pokeproxy_upload_timeout,
        )
        signatures = request.headers.getlist("x-grd-signature")
        if len(signatures) != 1 or not verify_signature(
            state.hmac_key, body, signatures[0]
        ):
            raise InputError(401, "invalid signature")
        key = make_cache_key(hashlib.sha256(body).hexdigest())
        pokemon = await state.cache.get(key, correlation)
        if pokemon is None:
            try:
                pokemon = decode_pokemon(body)
            except ValueError:
                raise InputError(400, "invalid protobuf") from None
            await state.cache.put(key, pokemon, correlation)
        rule = match_pokemon(pokemon, state.rules)
        if rule is None:
            response, outcome = JSONResponse({}), "unmatched"
        else:
            stats.rule_matches.labels(f"rule_{state.rules.index(rule)}").inc()
            response = await forward(request, rule, pokemon, len(body), correlation)
            outcome = (
                "forwarded" if response.status_code < 400 else "downstream_http_error"
            )
        status = response.status_code
    except InputError as exc:
        status = exc.status
        outcome = exc.message.replace(
            " ", "_"
        )  # Fixed application-defined values only.
        response = JSONResponse(
            {"error": exc.message, "request_id": correlation}, status_code=status
        )
    except ClientDisconnect:
        status, outcome = 499, "client_disconnected"
        response = Response(status_code=status)
    except asyncio.CancelledError:
        status, outcome = 503, "cancelled"
        raise
    except Exception as exc:
        event("request_failed", request_id=correlation, error_type=type(exc).__name__)
        response = JSONResponse(
            {"error": "internal request failure", "request_id": correlation},
            status_code=500,
        )
    finally:
        if admitted:
            state.active -= 1
            stats.inflight.dec()
        elapsed = time.monotonic() - start
        stats.requests.labels(outcome, str(status)).inc()
        stats.duration.observe(elapsed)
        event(
            "request_complete",
            request_id=correlation,
            outcome=outcome,
            status=status,
            duration_seconds=elapsed,
        )
    response.headers["x-request-id"] = correlation
    return response
