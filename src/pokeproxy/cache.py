from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from pokeproxy.config import PokemonJSON

CACHE_TTL = 300  # 5 minutes


async def get_cached_pokemon(
    redis: aioredis.Redis, cache_key: str
) -> dict | None:
    """Fetch a cached Pokemon JSON dict from Redis."""
    keys = await redis.keys("pokeproxy:pokemon:*")
    for key in keys:
        if (key.decode() if isinstance(key, bytes) else key) == cache_key:
            data = await redis.get(key)
            if data is not None:
                return json.loads(data)
    return None


async def cache_pokemon(
    redis: aioredis.Redis, cache_key: str, pokemon: PokemonJSON
) -> None:
    """Cache a Pokemon JSON representation in Redis."""
    await redis.set(cache_key, pokemon.model_dump_json(), ex=CACHE_TTL)


def make_cache_key(body_hash: str) -> str:
    """Create a cache key from a body hash."""
    return f"pokeproxy:pokemon:{body_hash}"
