import asyncio

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from pokeproxy.config import decode_pokemon
from tests import test_hardening
from tests.test_hardening import BODY, response, send

running = test_hardening.running


async def test_metrics_follow_auth_cache_routing_and_forwarding(running):
    registry = running.state.stats.registry
    sample = registry.get_sample_value
    assert sample("pokeproxy_requests_received_total") == 0
    assert (await running.client.post("/stream", content=BODY)).status_code == 401
    assert (await send(running, body=b"invalid protobuf")).status_code == 400
    assert sample("pokeproxy_rule_matches_total", {"rule": "rule_0"}) is None
    assert len(running.sent) == 0

    assert (await send(running)).status_code == 200
    running.redis.get.return_value = decode_pokemon(BODY).model_dump_json()
    assert (await send(running)).status_code == 200  # A hit is not deduplication.
    assert sample("pokeproxy_rule_matches_total", {"rule": "rule_0"}) == 2
    assert (
        sample("pokeproxy_forward_total", {"rule": "rule_0", "outcome": "success"}) == 2
    )
    assert sample("pokeproxy_cache_total", {"outcome": "hit"}) == 1
    assert sample("pokeproxy_cache_total", {"outcome": "miss"}) == 2
    assert sample("pokeproxy_cache_total", {"outcome": "write"}) == 1

    running.state.rules = ()
    assert (await send(running)).json() == {}
    assert (
        sample("pokeproxy_requests_total", {"outcome": "unmatched", "status": "200"})
        == 1
    )
    assert sample("pokeproxy_rule_matches_total", {"rule": "rule_0"}) == 2
    assert sample("pokeproxy_requests_received_total") == 5
    assert sample("pokeproxy_request_duration_seconds_count") == 5
    assert sample("pokeproxy_forward_duration_seconds_count", {"rule": "rule_0"}) == 2
    assert sample("pokeproxy_inflight") == 0
    before = sample("pokeproxy_requests_received_total")
    assert (await running.client.get("/metrics")).status_code == 200
    assert (await running.client.get("/health")).status_code == 200
    assert sample("pokeproxy_requests_received_total") == before


async def test_cache_errors_are_not_failed_delivery(running):
    running.redis.get.side_effect = RedisConnectionError("unavailable")
    running.redis.set.side_effect = RedisConnectionError("unavailable")
    assert (await send(running)).status_code == 200
    sample = running.state.stats.registry.get_sample_value
    for operation in ("read_error", "write_error"):
        assert sample("pokeproxy_cache_total", {"outcome": operation}) == 1
    assert sample("pokeproxy_cache_total", {"outcome": "miss"}) is None
    assert (
        sample("pokeproxy_requests_total", {"outcome": "forwarded", "status": "200"})
        == 1
    )


async def test_downstream_4xx_is_a_forward_failure_not_invalid_hmac(running):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response(422))
    ) as client:
        running.state.http_client = client
        assert (await send(running)).status_code == 422
    sample = running.state.stats.registry.get_sample_value
    assert (
        sample("pokeproxy_forward_total", {"rule": "rule_0", "outcome": "http_error"})
        == 1
    )
    assert (
        sample(
            "pokeproxy_requests_total",
            {"outcome": "downstream_http_error", "status": "422"},
        )
        == 1
    )
    assert (
        sample(
            "pokeproxy_requests_total",
            {"outcome": "invalid_signature", "status": "401"},
        )
        is None
    )


async def test_started_and_completed_counts_differ_during_inflight_work(running):
    entered = asyncio.Event()

    async def slow(req):
        entered.set()
        await asyncio.sleep(10)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        running.state.http_client = client
        task = asyncio.create_task(send(running))
        await entered.wait()
        sample = running.state.stats.registry.get_sample_value
        assert sample("pokeproxy_requests_received_total") == 1
        assert sample("pokeproxy_request_duration_seconds_count") == 0
        assert sample("pokeproxy_inflight") == 1
        assert sample("pokeproxy_rule_matches_total", {"rule": "rule_0"}) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert sample("pokeproxy_request_duration_seconds_count") == 1
    assert sample("pokeproxy_inflight") == 0
    assert (
        sample("pokeproxy_forward_total", {"rule": "rule_0", "outcome": "cancelled"})
        == 1
    )
