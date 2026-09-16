from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Pokemon(_message.Message):
    __slots__ = ("number", "name", "type_one", "type_two", "total", "hit_points", "attack", "defense", "special_attack", "special_defense", "speed", "generation", "legendary")
    NUMBER_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_ONE_FIELD_NUMBER: _ClassVar[int]
    TYPE_TWO_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    HIT_POINTS_FIELD_NUMBER: _ClassVar[int]
    ATTACK_FIELD_NUMBER: _ClassVar[int]
    DEFENSE_FIELD_NUMBER: _ClassVar[int]
    SPECIAL_ATTACK_FIELD_NUMBER: _ClassVar[int]
    SPECIAL_DEFENSE_FIELD_NUMBER: _ClassVar[int]
    SPEED_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    LEGENDARY_FIELD_NUMBER: _ClassVar[int]
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
    def __init__(self, number: _Optional[int] = ..., name: _Optional[str] = ..., type_one: _Optional[str] = ..., type_two: _Optional[str] = ..., total: _Optional[int] = ..., hit_points: _Optional[int] = ..., attack: _Optional[int] = ..., defense: _Optional[int] = ..., special_attack: _Optional[int] = ..., special_defense: _Optional[int] = ..., speed: _Optional[int] = ..., generation: _Optional[int] = ..., legendary: bool = ...) -> None: ...
