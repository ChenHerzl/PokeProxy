from __future__ import annotations

import base64
import binascii
from typing import Literal

from google.protobuf.message import DecodeError
from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pokeproxy.proto import pokemon_pb2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore", hide_input_in_errors=True
    )

    pokeproxy_secret: SecretStr
    pokeproxy_config: str
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("pokeproxy_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        try:
            decoded = base64.b64decode(value.get_secret_value(), validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("POKEPROXY_SECRET must be valid base64") from None
        if len(decoded) < 32:
            raise ValueError("POKEPROXY_SECRET must decode to at least 32 bytes")
        return value

    @property
    def hmac_key(self) -> bytes:
        return base64.b64decode(self.pokeproxy_secret.get_secret_value(), validate=True)


class PokemonJSON(BaseModel):
    number: int
    name: str
    type_one: str
    type_two: str
    total: int
    hit_points: int
    attack: int
    defense: int
    special_attack: int
    special_defense: int
    speed: int
    generation: int
    legendary: bool


PokemonField = Literal[
    "number",
    "name",
    "type_one",
    "type_two",
    "total",
    "hit_points",
    "attack",
    "defense",
    "special_attack",
    "special_defense",
    "speed",
    "generation",
    "legendary",
]

Operator = Literal["==", "!=", ">", "<"]


class MatchCondition(BaseModel):
    field: PokemonField
    operator: Operator
    value: int | str | bool


class Rule(BaseModel):
    url: str
    reason: str
    conditions: list[MatchCondition]


def decode_pokemon(body: bytes) -> PokemonJSON:
    """Decode protobuf bytes into PokemonJSON. Raises ValueError on failure."""
    try:
        proto = pokemon_pb2.Pokemon()
        proto.ParseFromString(body)
    except DecodeError as e:
        raise ValueError(f"Invalid protobuf: {e}") from e

    if not proto.name:
        raise ValueError("Decoded protobuf has empty name — likely garbage input")

    return PokemonJSON(
        number=proto.number,
        name=proto.name,
        type_one=proto.type_one,
        type_two=proto.type_two,
        total=proto.total,
        hit_points=proto.hit_points,
        attack=proto.attack,
        defense=proto.defense,
        special_attack=proto.special_attack,
        special_defense=proto.special_defense,
        speed=proto.speed,
        generation=proto.generation,
        legendary=proto.legendary,
    )
