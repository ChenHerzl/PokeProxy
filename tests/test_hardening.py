import asyncio
import base64
import gzip
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request

from pokeproxy.config import Settings, decode_pokemon
from pokeproxy.http import InputError, read_body
from pokeproxy.main import app, close_resource, lifespan
from pokeproxy.proto.pokemon_pb2 import Pokemon
from pokeproxy.proxy import verify_signature
from pokeproxy.rules import load_rules
from pokeproxy.stats import StatsRegistry

SECRET = b"x" * 32
BODY = Pokemon(
    number=6, name="Charizard", type_one="Fire", attack=84, generation=1
).SerializeToString()


class RawStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def response(status=200, body=b'{"ok":true}', headers=None):
    return httpx.Response(status, stream=RawStream([body]), headers=headers)


@pytest.fixture
async def running(monkeypatch, tmp_path):
    config = tmp_path / "rules.json"
    config.write_text(Path("config/rules.json").read_text())  # noqa: ASYNC240
    monkeypatch.setenv("POKEPROXY_SECRET", base64.b64encode(SECRET).decode())
    monkeypatch.setenv("POKEPROXY_CONFIG", str(config))
    redis = SimpleNamespace(
        get=AsyncMock(return_value=None), set=AsyncMock(), aclose=AsyncMock()
    )
    monkeypatch.setattr("pokeproxy.main.aioredis.from_url", lambda *a, **kw: redis)
    sent = []

    async def downstream(req):
        sent.append(req)
        return response()

    async with lifespan(app):
        original = app.state.http_client
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(downstream)
        ) as downstream_client:
            app.state.http_client = downstream_client
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://proxy"
            ) as client:
                yield SimpleNamespace(
                    client=client,
                    redis=redis,
                    sent=sent,
                    state=app.state,
                    config=config,
                    original=original,
                )
    assert original.is_closed
    redis.aclose.assert_awaited_once()


async def send(running, body=BODY, **headers):
    return await running.client.post(
        "/stream",
        content=body,
        headers={
            "X-Grd-Signature": hmac.new(SECRET, body, hashlib.sha256).hexdigest(),
            **headers,
        },
    )


async def test_full_flow_cache_hit_still_forwards(running):
    first = await send(running, **{"X-Request-ID": "run_1"})
    assert first.status_code == 200
    assert json.loads(running.sent[0].content) == decode_pokemon(BODY).model_dump()
    assert running.sent[0].headers["x-grd-reason"] == "strong fire pokemon"
    assert running.sent[0].headers["x-request-id"] == "run_1"
    assert "x-grd-signature" not in running.sent[0].headers
    running.redis.set.assert_awaited_once()
    assert running.redis.set.call_args.kwargs["ex"] == 300
    running.redis.get.return_value = decode_pokemon(BODY).model_dump_json()
    running.config.unlink()  # Rules are already loaded; no per-request disk access.
    assert (await send(running)).status_code == 200
    assert len(running.sent) == 2
    assert running.redis.get.await_count == 2
    counters = (await running.client.get("/stats")).json()["rule_0"]
    assert counters["request_count"] == 2
    assert counters["bytes_received"] == len(BODY) * 2
    assert counters["error_count"] == 0


@pytest.mark.parametrize("cached", [b"broken", b"[]", b"null", b"{}", b'{"name":123}'])
async def test_corrupt_cache_is_miss(running, cached):
    running.redis.get.return_value = cached
    assert (await send(running)).status_code == 200
    assert len(running.sent) == 1


async def test_cache_outage_and_recovery(running):
    running.redis.get.side_effect = RedisConnectionError("secret-in-error")
    running.redis.set.side_effect = RedisConnectionError("secret-in-error")
    assert (await send(running)).status_code == 200
    assert (await running.client.get("/ready")).json()["cache"] == "degraded"
    running.redis.get.side_effect = None
    running.redis.set.side_effect = None
    assert (await send(running)).status_code == 200
    assert (await running.client.get("/ready")).json()["cache"] == "healthy"


async def test_cache_deadline(running):
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    running.redis.get.side_effect = slow
    running.redis.set.side_effect = slow
    running.state.settings.pokeproxy_redis_timeout = 0.01
    async with asyncio.timeout(0.5):
        assert (await send(running)).status_code == 200


@pytest.mark.parametrize("signature", ["", "é", "a" * 63, "z" * 64, "0" * 64])
def test_bad_signature_returns_false(signature):
    assert not verify_signature(SECRET, BODY, signature)


async def test_invalid_input_does_not_touch_cache(running):
    assert (await running.client.post("/stream", content=BODY)).status_code == 401
    assert (await send(running, b"garbage")).status_code == 400
    assert running.redis.get.await_count == 1
    assert not running.sent


async def test_unmatched_preserved(running):
    result = await send(
        running, Pokemon(name="Pikachu", type_one="Electric").SerializeToString()
    )
    assert result.status_code == 200 and result.json() == {}
    assert not running.sent


@pytest.mark.parametrize(
    "error,status",
    [
        (httpx.ConnectError("sensitive-url"), 502),
        (httpx.ReadTimeout("sensitive-url"), 504),
    ],
)
async def test_downstream_single_attempt(running, error, status):
    calls = 0

    async def failing(req):
        nonlocal calls
        calls += 1  # May already have accepted the POST before timing out.
        raise error

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
        running.state.http_client = client
        result = await send(running)
    assert result.status_code == status
    assert calls == 1
    assert "sensitive-url" not in result.text
    counters = running.state.stats.get("rule_0")
    assert counters.request_count == counters.error_count == 1


async def test_overall_downstream_deadline(running):
    async def slow(req):
        await asyncio.sleep(10)

    running.state.settings.pokeproxy_downstream_deadline = 0.01
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        running.state.http_client = client
        async with asyncio.timeout(0.5):
            assert (await send(running)).status_code == 504
    assert running.state.active == 0


async def test_raw_gzip_headers_and_request_filtering(running):
    raw = gzip.compress(b"downstream content")
    wire = RawStream([raw])

    async def handler(req):
        assert "x-remove" not in req.headers
        assert "content-encoding" not in req.headers
        assert "authorization" not in req.headers
        assert req.headers.get_list("x-grd-reason") == ["strong fire pokemon"]
        return httpx.Response(
            200,
            stream=wire,
            headers={
                "content-encoding": "gzip",
                "content-length": str(len(raw)),
                "connection": "x-internal",
                "x-internal": "remove",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        running.state.http_client = client
        result = await send(
            running,
            **{
                "connection": "x-remove",
                "x-remove": "value",
                "content-encoding": "identity",
                "authorization": "secret",
                "x-grd-reason": "spoof",
            },
        )
    assert result.content == b"downstream content"
    assert result.headers["content-length"] == str(len(raw))
    assert "x-internal" not in result.headers and "connection" not in result.headers
    assert wire.closed


async def test_response_limit_closes_stream(running):
    wire = RawStream([b"123", b"456"])
    running.state.settings.pokeproxy_max_response_bytes = 5
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=wire))
    ) as client:
        running.state.http_client = client
        assert (await send(running)).status_code == 502
    assert wire.closed


async def test_admission_and_cancellation(running):
    entered = asyncio.Event()

    async def slow(req):
        entered.set()
        await asyncio.sleep(10)

    running.state.settings.pokeproxy_max_inflight = 1
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        running.state.http_client = client
        task = asyncio.create_task(send(running))
        await entered.wait()
        assert (await send(running)).status_code == 503
        assert (await running.client.get("/health")).status_code == 200
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert running.state.active == 0
    assert running.state.stats.inflight._value.get() == 0


async def test_metrics_and_readiness(running):
    await send(running)
    await running.client.post("/stream", content=BODY)
    metrics = (await running.client.get("/metrics")).text
    assert 'pokeproxy_requests_total{outcome="forwarded",status="200"} 1.0' in metrics
    assert (
        'pokeproxy_requests_total{outcome="invalid_signature",status="401"} 1.0'
        in metrics
    )
    assert "pokeproxy_request_duration_seconds_bucket" in metrics
    assert "http://" not in metrics
    running.state.ready = False
    assert (await running.client.get("/ready")).status_code == 503
    assert (await send(running)).status_code == 503


@pytest.mark.parametrize("value", [0, -1, "nan", "inf"])
def test_invalid_budgets_rejected(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            pokeproxy_secret=base64.b64encode(SECRET).decode(),
            pokeproxy_config="config/rules.json",
            pokeproxy_http_timeout=value,
        )


@pytest.mark.parametrize(
    "value",
    ["http://redis", "redis://", "redis://host?socket_timeout=999", "redis://host/bad"],
)
def test_invalid_redis_url_redacted(value):
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            pokeproxy_secret=base64.b64encode(SECRET).decode(),
            pokeproxy_config="config/rules.json",
            redis_url=value,
        )
    assert value not in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"rules": {}},
        {"rules": [1]},
        {"rules": [{"url": "http://", "reason": "x", "match": ["attack>1"]}]},
        {
            "rules": [
                {"url": "http://user:secret@host", "reason": "x", "match": ["attack>1"]}
            ]
        },
        {
            "rules": [
                {"url": "http://host", "reason": "x\nspoof", "match": ["attack>1"]}
            ]
        },
        {"rules": [{"url": "http://host", "reason": "x", "match": []}]},
        {"rules": [{"url": "http://host", "reason": "x", "match": ["invalid"]}]},
    ],
)
def test_rules_reject_invalid_configuration(tmp_path, data):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError) as exc:
        load_rules(str(path))
    assert "secret" not in str(exc.value)


async def test_cleanup_errors_are_bounded():
    broken = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("secret")))
    await close_resource(broken, 0.01, "test")

    async def slow():
        await asyncio.sleep(10)

    async with asyncio.timeout(0.5):
        await close_resource(SimpleNamespace(aclose=slow), 0.01, "slow")


def asgi_request(chunks, headers=()):
    iterator = iter(chunks)
    calls = []

    async def receive():
        calls.append(1)
        chunk = next(iterator)
        return {"type": "http.request", "body": chunk, "more_body": True}

    return Request({"type": "http", "headers": list(headers)}, receive), calls


async def test_body_limit_stops_receiving_early():
    request, calls = asgi_request([b"123", b"456", b"must-not-read"])
    with pytest.raises(InputError) as exc:
        await read_body(request, 5, 1)
    assert exc.value.status == 413 and len(calls) == 2


@pytest.mark.parametrize("length", [b"-1", b"nan", b"99999999999999999999999999"])
async def test_bad_length(length):
    request, calls = asgi_request([], [(b"content-length", length)])
    with pytest.raises(InputError) as exc:
        await read_body(request, 10, 1)
    assert exc.value.status == 400 and not calls


async def test_slow_upload_timeout():
    async def receive():
        await asyncio.sleep(10)

    request = Request({"type": "http", "headers": []}, receive)
    with pytest.raises(InputError) as exc:
        await read_body(request, 10, 0.01)
    assert exc.value.status == 408


async def test_bounded_metrics_storage():
    stats = StatsRegistry()
    initial = len(list(stats.registry.collect()))
    for _ in range(10000):
        stats.duration.observe(0.1)
    assert len(list(stats.registry.collect())) == initial
    samples = next(iter(stats.duration.collect())).samples
    assert len(samples) < 30
    assert not hasattr(stats.get("rule_0"), "_response_times")


async def test_partial_startup_closes_http(monkeypatch, tmp_path):
    path = tmp_path / "rules.json"
    path.write_text('{"rules": []}')
    monkeypatch.setenv("POKEPROXY_CONFIG", str(path))
    monkeypatch.setenv("POKEPROXY_SECRET", base64.b64encode(SECRET).decode())
    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr("pokeproxy.main.httpx.AsyncClient", lambda **kw: client)

    def broken(*args, **kwargs):
        raise RuntimeError("initialization failed")

    monkeypatch.setattr("pokeproxy.main.aioredis.from_url", broken)
    with pytest.raises(RuntimeError), pytest.MonkeyPatch.context():
        async with lifespan(app):
            pytest.fail("must not become ready")
    client.aclose.assert_awaited_once()
    assert app.state.ready is False


async def test_close_failure_does_not_skip_other_client(monkeypatch, tmp_path):
    path = tmp_path / "rules.json"
    path.write_text('{"rules": []}')
    monkeypatch.setenv("POKEPROXY_CONFIG", str(path))
    monkeypatch.setenv("POKEPROXY_SECRET", base64.b64encode(SECRET).decode())
    redis = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("failed")))
    monkeypatch.setattr("pokeproxy.main.aioredis.from_url", lambda *a, **kw: redis)
    async with lifespan(app):
        client = app.state.http_client
    assert client.is_closed and not app.state.ready


async def test_startup_rejects_missing_rules_before_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("POKEPROXY_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("POKEPROXY_SECRET", base64.b64encode(SECRET).decode())

    def unexpected(**kwargs):
        pytest.fail("must validate rules before allocating clients")

    monkeypatch.setattr("pokeproxy.main.httpx.AsyncClient", unexpected)
    with pytest.raises(ValueError, match="Routing configuration"):
        async with lifespan(app):
            pass


async def test_logs_have_context_without_secrets(running):
    import logging

    from pokeproxy.logging import JSONFormatter, logger

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(JSONFormatter().format(record))

    handler = Capture()
    logger.addHandler(handler)
    try:
        running.redis.get.side_effect = RedisConnectionError("private-password")
        await send(
            running, **{"X-Request-ID": "safe_id", "Authorization": "private-auth"}
        )
    finally:
        logger.removeHandler(handler)
    encoded = "\n".join(records)
    assert "private-password" not in encoded and "private-auth" not in encoded
    assert base64.b64encode(SECRET).decode() not in encoded
    entries = [json.loads(line) for line in records]
    assert any(
        e.get("event") == "request_complete" and e.get("request_id") == "safe_id"
        for e in entries
    )
    assert any(
        e.get("event") == "cache_degraded" and e.get("error_type") == "ConnectionError"
        for e in entries
    )


async def test_shared_client_does_not_retain_downstream_cookies(running):
    from http.cookiejar import CookieJar

    from pokeproxy.http import RejectCookies

    async def handler(request):
        assert "cookie" not in request.headers
        return response(headers={"set-cookie": "session=private; Path=/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        cookies=CookieJar(policy=RejectCookies()),
    ) as client:
        running.state.http_client = client
        await send(running)
        await send(running)
        assert len(client.cookies) == 0
    running.original.cookies.extract_cookies(
        httpx.Response(
            200,
            headers={"set-cookie": "session=private; Path=/"},
            request=httpx.Request("GET", "http://example.test/"),
        )
    )
    assert len(running.original.cookies) == 0


async def test_downstream_status_and_counter_population(running):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response(503))
    ) as client:
        running.state.http_client = client
        assert (await send(running)).status_code == 503
    counters = running.state.stats.get("rule_0")
    assert counters.request_count == counters.error_count == 1
    assert counters.to_dict()["error_rate"] == 1


def test_rule_order_and_explicit_empty_configuration(tmp_path):
    from pokeproxy.rules import match_pokemon

    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "url": "http://first",
                        "reason": "first",
                        "match": ["attack>80", "type_one==Fire"],
                    },
                    {"url": "http://second", "reason": "second", "match": ["attack>1"]},
                ]
            }
        )
    )
    rules = load_rules(str(path))
    assert match_pokemon(decode_pokemon(BODY), rules).reason == "first"
    path.write_text('{"rules": []}')
    assert load_rules(str(path)) == []


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("attack==84", True),
        ("attack!=84", False),
        ("attack<85", True),
        ("type_one!=Water", True),
        ("legendary==false", True),
    ],
)
def test_rule_operator_semantics(expression, expected):
    from pokeproxy.rules import evaluate_condition, parse_condition

    assert (
        evaluate_condition(parse_condition(expression), decode_pokemon(BODY))
        is expected
    )


async def test_declared_size_mismatch_and_duplicate_signatures(running):
    result = await send(running, **{"content-length": "1"})
    assert result.status_code == 400
    signature = hmac.new(SECRET, BODY, hashlib.sha256).hexdigest()
    result = await running.client.post(
        "/stream",
        content=BODY,
        headers=[("x-grd-signature", signature), ("x-grd-signature", signature)],
    )
    assert result.status_code == 401 and not running.sent


async def test_unexpected_failure_is_redacted(running, monkeypatch):
    def broken(*args):
        raise RuntimeError("sensitive-value")

    monkeypatch.setattr("pokeproxy.proxy.match_pokemon", broken)
    result = await send(running)
    assert result.status_code == 500
    assert "sensitive-value" not in result.text
    assert result.json()["request_id"] == result.headers["x-request-id"]
    assert running.state.active == 0
