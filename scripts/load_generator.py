#!/usr/bin/env python3
"""
Load generator for PokeProxy.

Sends synthetic Pokemon protobuf payloads with valid HMAC signatures
to the /stream endpoint. Useful for testing under sustained load.

Usage:
    python scripts/load_generator.py [--url URL] [--rps RPS] [--duration SECONDS]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import random
import sys
import time

import httpx

# Add parent to path so we can import proto
sys.path.insert(0, "src")
from pokeproxy.proto import pokemon_pb2

# Sample Pokemon data for generating realistic payloads
POKEMON_DATA = [
    {"number": 6, "name": "Charizard", "type_one": "Fire", "type_two": "Flying", "total": 534, "hit_points": 78, "attack": 84, "defense": 78, "special_attack": 109, "special_defense": 85, "speed": 100, "generation": 1, "legendary": False},
    {"number": 150, "name": "Mewtwo", "type_one": "Psychic", "type_two": "", "total": 680, "hit_points": 106, "attack": 110, "defense": 90, "special_attack": 154, "special_defense": 90, "speed": 130, "generation": 1, "legendary": True},
    {"number": 143, "name": "Snorlax", "type_one": "Normal", "type_two": "", "total": 540, "hit_points": 160, "attack": 110, "defense": 65, "special_attack": 65, "special_defense": 110, "speed": 30, "generation": 1, "legendary": False},
    {"number": 248, "name": "Tyranitar", "type_one": "Rock", "type_two": "Dark", "total": 600, "hit_points": 100, "attack": 134, "defense": 110, "special_attack": 95, "special_defense": 100, "speed": 61, "generation": 2, "legendary": False},
    {"number": 249, "name": "Lugia", "type_one": "Psychic", "type_two": "Flying", "total": 680, "hit_points": 106, "attack": 90, "defense": 130, "special_attack": 90, "special_defense": 154, "speed": 110, "generation": 2, "legendary": True},
    {"number": 376, "name": "Metagross", "type_one": "Steel", "type_two": "Psychic", "total": 600, "hit_points": 80, "attack": 135, "defense": 130, "special_attack": 95, "special_defense": 90, "speed": 70, "generation": 3, "legendary": False},
    {"number": 445, "name": "Garchomp", "type_one": "Dragon", "type_two": "Ground", "total": 600, "hit_points": 108, "attack": 130, "defense": 95, "special_attack": 80, "special_defense": 85, "speed": 102, "generation": 4, "legendary": False},
    {"number": 257, "name": "Blaziken", "type_one": "Fire", "type_two": "Fighting", "total": 530, "hit_points": 80, "attack": 120, "defense": 70, "special_attack": 110, "special_defense": 70, "speed": 80, "generation": 3, "legendary": False},
    {"number": 131, "name": "Lapras", "type_one": "Water", "type_two": "Ice", "total": 535, "hit_points": 130, "attack": 85, "defense": 80, "special_attack": 85, "special_defense": 95, "speed": 60, "generation": 1, "legendary": False},
    {"number": 149, "name": "Dragonite", "type_one": "Dragon", "type_two": "Flying", "total": 600, "hit_points": 91, "attack": 134, "defense": 95, "special_attack": 100, "special_defense": 100, "speed": 80, "generation": 1, "legendary": False},
    {"number": 59, "name": "Arcanine", "type_one": "Fire", "type_two": "", "total": 555, "hit_points": 90, "attack": 110, "defense": 80, "special_attack": 100, "special_defense": 80, "speed": 95, "generation": 1, "legendary": False},
    {"number": 212, "name": "Scizor", "type_one": "Bug", "type_two": "Steel", "total": 500, "hit_points": 70, "attack": 130, "defense": 100, "special_attack": 55, "special_defense": 80, "speed": 65, "generation": 2, "legendary": False},
]


def make_pokemon_proto(data: dict) -> bytes:
    """Create a serialized protobuf Pokemon message."""
    pokemon = pokemon_pb2.Pokemon()
    pokemon.number = data["number"]
    pokemon.name = data["name"]
    pokemon.type_one = data["type_one"]
    pokemon.type_two = data["type_two"]
    pokemon.total = data["total"]
    pokemon.hit_points = data["hit_points"]
    pokemon.attack = data["attack"]
    pokemon.defense = data["defense"]
    pokemon.special_attack = data["special_attack"]
    pokemon.special_defense = data["special_defense"]
    pokemon.speed = data["speed"]
    pokemon.generation = data["generation"]
    pokemon.legendary = data["legendary"]
    return pokemon.SerializeToString()


def sign_payload(secret: bytes, body: bytes) -> str:
    """Compute HMAC-SHA256 signature for a payload."""
    return hmac.new(key=secret, msg=body, digestmod=hashlib.sha256).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="PokeProxy load generator")
    parser.add_argument("--url", default="http://localhost:8000/stream", help="Target URL")
    parser.add_argument("--rps", type=float, default=10.0, help="Requests per second")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds (0 = infinite)")
    parser.add_argument("--secret", default="bG9jYWwtZGV2ZWxvcG1lbnQtb25seS1zZWNyZXQtMzIh", help="Base64-encoded HMAC secret")
    args = parser.parse_args()

    secret = base64.b64decode(args.secret)
    interval = 1.0 / args.rps
    end_time = time.time() + args.duration if args.duration > 0 else float("inf")

    print(f"Sending traffic to {args.url} at ~{args.rps} rps")
    if args.duration > 0:
        print(f"Duration: {args.duration}s")
    else:
        print("Duration: infinite (Ctrl+C to stop)")

    sent = 0
    errors = 0

    with httpx.Client(timeout=10.0) as client:
        while time.time() < end_time:
            data = random.choice(POKEMON_DATA)
            body = make_pokemon_proto(data)
            signature = sign_payload(secret, body)

            try:
                resp = client.post(
                    args.url,
                    content=body,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Grd-Signature": signature,
                    },
                )
                sent += 1
                if resp.status_code >= 400:
                    errors += 1
                    if sent % 100 == 0:
                        print(f"  [{sent}] {data['name']} -> HTTP {resp.status_code}")
                elif sent % 50 == 0:
                    print(f"  [{sent}] {data['name']} -> HTTP {resp.status_code}")
            except httpx.HTTPError as e:
                errors += 1
                sent += 1
                if sent % 50 == 0:
                    print(f"  [{sent}] ERROR: {e}")

            time.sleep(interval)

    print(f"\nDone. Sent: {sent}, Errors: {errors}, Error rate: {errors/sent*100:.1f}%" if sent else "\nNo requests sent.")


if __name__ == "__main__":
    main()
