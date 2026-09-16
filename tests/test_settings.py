"""Regression coverage for documented configuration and startup (004, 012)."""

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pokeproxy.config import Settings
from pokeproxy.main import app


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    for name in (
        "POKEPROXY_SECRET",
        "POKEPROXY_HMAC_KEY",
        "POKEPROXY_CONFIG",
        "REDIS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


def test_documented_environment_and_redaction(monkeypatch):
    raw = b"a-test-key-with-at-least-32-bytes!!"
    encoded = base64.b64encode(raw).decode()
    monkeypatch.setenv("POKEPROXY_SECRET", encoded)
    settings = Settings()
    assert settings.hmac_key == raw
    assert encoded not in repr(settings)
    assert encoded not in settings.model_dump_json()


@pytest.mark.parametrize(
    "secret",
    ["", "!!!!", "é", "YQ", "YQ==\n", "YQ==", base64.b64encode(b"x" * 31).decode()],
)
def test_invalid_secrets_fail_before_clients_are_created(monkeypatch, secret):
    monkeypatch.setenv("POKEPROXY_SECRET", secret)

    def unexpected_client(*args, **kwargs):
        pytest.fail("Invalid settings must fail before allocating clients")

    monkeypatch.setattr("pokeproxy.main.httpx.AsyncClient", unexpected_client)
    with pytest.raises(ValidationError) as caught, TestClient(app):
        pass
    assert "POKEPROXY_SECRET" in str(caught.value)
    assert "input_value" not in str(caught.value)
    if secret:
        assert secret not in str(caught.value)


def test_missing_secret_rejected():
    with pytest.raises(ValidationError, match="pokeproxy_secret"):
        Settings()


def test_example_dotenv_starts_application():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    Path(".env").write_text(example.read_text())
    with TestClient(app) as client:
        assert app.state.hmac_key == Settings().hmac_key
        assert client.get("/health").json() == {"status": "alive"}
