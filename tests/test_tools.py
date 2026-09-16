import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from mock_service.main import app
from pokeproxy.config import decode_pokemon
from scripts import load_generator, verify
from tests.test_hardening import BODY, SECRET


def test_mock_retention_correlation_and_validation(monkeypatch):
    monkeypatch.setenv("MOCK_MAX_RECEIPTS", "2")
    with TestClient(app) as client:
        pokemon = decode_pokemon(BODY).model_dump()
        for correlation in ["old", "one", "two"]:
            assert (
                client.post(
                    "/pokemon", json=pokemon, headers={"x-request-id": correlation}
                ).status_code
                == 200
            )
        assert len(client.get("/received").json()) == 2
        assert client.get("/received", params={"request_id": "old"}).json() == []
        assert len(client.get("/received", params={"request_id": "one"}).json()) == 1
        assert client.post("/pokemon", content="invalid").status_code == 400
        assert client.post("/pokemon", json=[]).status_code == 400


@pytest.mark.parametrize(
    "args",
    [
        ["--rps", "0"],
        ["--rps", "nan"],
        ["--rps", "-1"],
        ["--duration", "-1"],
        ["--secret", "!!!!"],
    ],
)
def test_load_generator_rejects_invalid_args(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["load_generator", *args])
    with pytest.raises(SystemExit) as exc:
        load_generator.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("mode", ["good", "wrong", "missing", "http_error"])
def test_verifier_checks_receipt(monkeypatch, mode):
    receipt = []

    def handler(request):
        if request.url.path == "/stream":
            from pokeproxy.config import decode_pokemon

            receipt.append(
                {
                    "pokemon": decode_pokemon(request.content).model_dump(),
                    "reason": "expected",
                    "request_id": request.headers["x-request-id"],
                }
            )
            if mode == "wrong":
                receipt[0]["pokemon"]["name"] = "wrong"
            return httpx.Response(502 if mode == "http_error" else 200, json={})
        return httpx.Response(200, json=[] if mode == "missing" else receipt)

    real_client = httpx.Client
    monkeypatch.setattr(
        verify.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    if mode == "good":
        assert verify.verify(
            "http://proxy", "http://mock", SECRET, "expected", timeout=0.02
        )
    else:
        with pytest.raises((ValueError, httpx.HTTPError)):
            verify.verify(
                "http://proxy", "http://mock", SECRET, "expected", timeout=0.02
            )


def test_load_generator_pacing_and_error_exit(monkeypatch, capsys):
    now = [0.0]
    monkeypatch.setattr(load_generator.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        load_generator.time,
        "sleep",
        lambda interval: now.__setitem__(0, now[0] + interval),
    )
    monkeypatch.setattr(
        sys, "argv", ["load_generator", "--rps", "10", "--duration", "1"]
    )

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            now[0] += (
                0.05  # Fast enough for 10 RPS; should not add 0.1s after each response.
            )
            return httpx.Response(500)

    monkeypatch.setattr(load_generator.httpx, "Client", Client)
    with pytest.raises(SystemExit) as exc:
        load_generator.main()
    assert exc.value.code == 1
    assert "Achieved rate:" in capsys.readouterr().out
    assert now[0] >= 1
