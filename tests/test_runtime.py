"""Real sockets, Uvicorn signals and optional real Redis integration."""

# Nested contexts make process/thread teardown order explicit in integration tests.
# ruff: noqa: SIM117
import base64
import concurrent.futures
import contextlib
import hashlib
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import redis.asyncio as aioredis

from pokeproxy.cache import PokemonCache, make_cache_key
from pokeproxy.config import Settings, decode_pokemon
from pokeproxy.stats import StatsRegistry
from scripts.verify import verify
from tests.test_hardening import BODY, SECRET

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def server(module, environment, log_path):
    with socket.socket() as listener, log_path.open("w+") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            module,
            "--fd",
            str(listener.fileno()),
            "--timeout-graceful-shutdown",
            "3",
            "--no-access-log",
        ]
        process = subprocess.Popen(  # noqa: S603 -- fixed test executable and local modules
            command,
            cwd=ROOT,
            env={**os.environ, **environment},
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=log,
        )
        try:
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 10
            with httpx.Client(timeout=0.2, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        pytest.fail(f"server exited: {log.read()}")
                    try:
                        if client.get(url + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.03)
                else:
                    pytest.fail("server startup timed out")
            yield process, url
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def environment(tmp_path, downstream):
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "url": downstream,
                        "reason": "strong fire pokemon",
                        "match": ["type_one==Fire", "attack>80", "generation<4"],
                    }
                ]
            }
        )
    )
    return {
        "POKEPROXY_SECRET": base64.b64encode(SECRET).decode(),
        "POKEPROXY_CONFIG": str(rules),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:1/0"),
        "POKEPROXY_REDIS_TIMEOUT": "0.05",
        "POKEPROXY_DOWNSTREAM_DEADLINE": "0.3",
    }


@pytest.mark.parametrize("redis_available", [True, False])
def test_real_signed_receipt_and_no_match_failure(tmp_path, redis_available):
    with server("mock_service.main:app", {}, tmp_path / "mock.log") as (_, mock):
        env = environment(tmp_path, mock + "/pokemon")
        if not redis_available:
            env["REDIS_URL"] = "redis://127.0.0.1:1/0"
        with server("pokeproxy.main:app", env, tmp_path / "proxy.log") as (_, proxy):
            assert verify(proxy, mock, SECRET, "strong fire pokemon", timeout=2)
            # Same payload is still forwarded a second time, with independent receipt correlation.
            assert verify(proxy, mock, SECRET, "strong fire pokemon", timeout=2)
            with httpx.Client() as client:
                assert client.get(proxy + "/ready").status_code == 200
        logs = (tmp_path / "proxy.log").read_text()
        assert '"event": "shutdown_complete"' in logs
        Path(env["POKEPROXY_CONFIG"]).write_text('{"rules": []}')
        with server("pokeproxy.main:app", env, tmp_path / "unmatched.log") as (
            _,
            proxy,
        ):
            with pytest.raises(ValueError, match="receipt not found"):
                verify(proxy, mock, SECRET, "strong fire pokemon", timeout=0.2)


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_signal_drains_request_with_downstream_failure(tmp_path, stop_signal):
    entered = threading.Event()
    release = threading.Event()

    class HangingDownstream(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            entered.set()
            release.wait(timeout=5)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), HangingDownstream) as downstream:
        thread = threading.Thread(target=downstream.serve_forever, daemon=True)
        thread.start()
        try:
            env = environment(
                tmp_path, f"http://127.0.0.1:{downstream.server_port}/pokemon"
            )
            with server("pokeproxy.main:app", env, tmp_path / "proxy.log") as (
                process,
                proxy,
            ):
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

                    def send():
                        return httpx.post(
                            proxy + "/stream",
                            content=BODY,
                            timeout=4,
                            headers={
                                "X-Grd-Signature": hmac.new(
                                    SECRET, BODY, hashlib.sha256
                                ).hexdigest()
                            },
                        )

                    pending = pool.submit(send)
                    assert entered.wait(timeout=3)
                    process.send_signal(stop_signal)
                    assert pending.result(timeout=4).status_code == 504
                    process.wait(timeout=4)
                    assert process.returncode in (0, -stop_signal)
            logs = (tmp_path / "proxy.log").read_text()
            assert '"event": "shutdown_complete"' in logs
            assert '"active_requests": 0' in logs
        finally:
            release.set()
            downstream.shutdown()
            thread.join(timeout=2)


@pytest.mark.skipif(
    not os.environ.get("TEST_REDIS_URL"),
    reason="Set TEST_REDIS_URL to an isolated Redis instance",
)
async def test_real_redis_hit_miss_and_expiry():
    settings = Settings(
        _env_file=None,
        pokeproxy_secret=base64.b64encode(SECRET).decode(),
        pokeproxy_config="unused",
        redis_url=os.environ["TEST_REDIS_URL"],
        pokeproxy_cache_ttl=1,
        pokeproxy_redis_timeout=1,
    )
    client = aioredis.from_url(os.environ["TEST_REDIS_URL"])
    cache = PokemonCache(client, settings, StatsRegistry())
    import uuid

    key = make_cache_key(uuid.uuid4().hex)
    try:
        assert await cache.get(key, "integration") is None
        pokemon = decode_pokemon(BODY)
        await cache.put(key, pokemon, "integration")
        assert await cache.get(key, "integration") == pokemon
        assert 0 < await client.pttl(key) <= 1000
        import asyncio

        async with asyncio.timeout(3):
            while await cache.get(key, "integration") is not None:  # noqa: ASYNC110 -- polling real server TTL
                await asyncio.sleep(0.05)
        assert cache.healthy is True
    finally:
        await client.delete(key)
        await client.aclose()
