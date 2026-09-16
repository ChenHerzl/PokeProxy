"""Deterministic real-traffic verification; never clears other runs' receipts."""

import argparse
import base64
import hashlib
import hmac
import os
import time
import uuid

import httpx

from pokeproxy.config import decode_pokemon
from pokeproxy.proto.pokemon_pb2 import Pokemon


def verify(
    proxy_url: str, mock_url: str, secret: bytes, reason: str, timeout: float = 10
) -> str:
    correlation = uuid.uuid4().hex
    body = Pokemon(
        number=6,
        name="Charizard",
        type_one="Fire",
        type_two="Flying",
        total=534,
        hit_points=78,
        attack=84,
        defense=78,
        special_attack=109,
        special_defense=85,
        speed=100,
        generation=1,
        legendary=False,
    ).SerializeToString()
    expected = decode_pokemon(body).model_dump()
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(
            proxy_url.rstrip("/") + "/stream",
            content=body,
            headers={
                "X-Grd-Signature": hmac.new(secret, body, hashlib.sha256).hexdigest(),
                "X-Request-ID": correlation,
                "Content-Type": "application/octet-stream",
            },
        )
        response.raise_for_status()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt = client.get(
                mock_url.rstrip("/") + "/received",
                params={"request_id": correlation},
                timeout=max(0.001, deadline - time.monotonic()),
            )
            receipt.raise_for_status()
            entries = receipt.json()
            if entries:
                if entries != [
                    {"pokemon": expected, "reason": reason, "request_id": correlation}
                ]:
                    raise ValueError(
                        "downstream receipt differs from expected JSON, reason or count"
                    )
                return correlation
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise ValueError("matching downstream receipt not found before deadline")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mock-url", default="http://127.0.0.1:8001")
    parser.add_argument("--reason", default="strong fire pokemon")
    args = parser.parse_args()
    try:
        secret = base64.b64decode(os.environ.get("POKEPROXY_SECRET", ""), validate=True)
        if len(secret) < 32:
            raise ValueError("POKEPROXY_SECRET must encode at least 32 bytes")
        correlation = verify(args.proxy_url, args.mock_url, secret, args.reason)
    except (ValueError, httpx.HTTPError) as exc:
        print(
            f"Verification failed ({type(exc).__name__}); check service logs and configuration"
        )
        raise SystemExit(1) from None
    print(f"Verified downstream JSON receipt: {correlation}")


if __name__ == "__main__":
    main()
