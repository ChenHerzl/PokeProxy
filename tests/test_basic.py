"""Basic tests for PokeProxy."""

from pokeproxy.config import PokemonJSON, decode_pokemon
from pokeproxy.proto import pokemon_pb2
from pokeproxy.rules import load_rules, match_pokemon, parse_condition


def _make_proto_bytes(**kwargs) -> bytes:
    pokemon = pokemon_pb2.Pokemon()
    defaults = {
        "number": 25,
        "name": "Pikachu",
        "type_one": "Electric",
        "type_two": "",
        "total": 320,
        "hit_points": 35,
        "attack": 55,
        "defense": 40,
        "special_attack": 50,
        "special_defense": 50,
        "speed": 90,
        "generation": 1,
        "legendary": False,
    }
    defaults.update(kwargs)
    for key, value in defaults.items():
        setattr(pokemon, key, value)
    return pokemon.SerializeToString()


def test_decode_pokemon():
    body = _make_proto_bytes(name="Pikachu", number=25)
    pokemon = decode_pokemon(body)
    assert pokemon.name == "Pikachu"
    assert pokemon.number == 25


def test_parse_condition():
    cond = parse_condition("attack>80")
    assert cond.field == "attack"
    assert cond.operator == ">"
    assert cond.value == 80


def test_load_rules():
    rules = load_rules("config/rules.json")
    assert len(rules) == 3
    assert rules[0].reason == "strong fire pokemon"


def test_match_pokemon_legendary():
    rules = load_rules("config/rules.json")
    pokemon = PokemonJSON(
        number=150,
        name="Mewtwo",
        type_one="Psychic",
        type_two="",
        total=680,
        hit_points=106,
        attack=110,
        defense=90,
        special_attack=154,
        special_defense=90,
        speed=130,
        generation=1,
        legendary=True,
    )
    result = match_pokemon(pokemon, rules)
    assert result is not None
    assert result.reason == "legendary pokemon"


def test_match_pokemon_no_match():
    rules = load_rules("config/rules.json")
    pokemon = PokemonJSON(
        number=25,
        name="Pikachu",
        type_one="Electric",
        type_two="",
        total=320,
        hit_points=35,
        attack=55,
        defense=40,
        special_attack=50,
        special_defense=50,
        speed=90,
        generation=1,
        legendary=False,
    )
    result = match_pokemon(pokemon, rules)
    assert result is None
