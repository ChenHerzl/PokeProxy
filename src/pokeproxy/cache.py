from __future__ import annotations

import asyncio

from redis.exceptions import RedisError

from pokeproxy.config import PokemonJSON
from pokeproxy.logging import event


def make_cache_key(body_hash: str) -> str:
    return f"pokeproxy:pokemon:{body_hash}"


class PokemonCache:
    def __init__(self, redis, settings, stats):
        self.redis, self.settings, self.stats = redis, settings, stats
        self.healthy: bool | None = None

    def failure(self, operation: str, error: Exception, request_id: str) -> None:
        self.healthy = False
        self.stats.cache.labels(f"{operation}_error").inc()
        event(
            "cache_degraded",
            operation=operation,
            error_type=type(error).__name__,
            request_id=request_id,
        )

    async def get(self, key: str, request_id: str) -> PokemonJSON | None:
        try:
            async with asyncio.timeout(self.settings.pokeproxy_redis_timeout):
                data = await self.redis.get(key)
            self.healthy = True
            if data is None:
                self.stats.cache.labels("miss").inc()
                return None
            pokemon = PokemonJSON.model_validate_json(data, strict=True)
            if not pokemon.name:
                raise ValueError("empty cached name")
            self.stats.cache.labels("hit").inc()
            return pokemon
        except (RedisError, TimeoutError, ValueError, TypeError) as exc:
            self.failure("read", exc, request_id)
            return None

    async def put(self, key: str, pokemon: PokemonJSON, request_id: str) -> None:
        try:
            async with asyncio.timeout(self.settings.pokeproxy_redis_timeout):
                await self.redis.set(
                    key, pokemon.model_dump_json(), ex=self.settings.pokeproxy_cache_ttl
                )
            self.healthy = True
            self.stats.cache.labels("write").inc()
        except (RedisError, TimeoutError) as exc:
            self.failure("write", exc, request_id)
