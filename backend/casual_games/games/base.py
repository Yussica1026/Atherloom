from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Any, Literal, Mapping, Protocol, TypedDict


ActorKind = Literal["user", "persona"]
JsonObject = dict[str, Any]


class EngineResult(TypedDict):
    winner: Literal["user", "persona", "draw"]
    outcome: str
    score: dict[str, int]
    notable_events: list[str]


@dataclass(frozen=True)
class Transition:
    state: JsonObject
    events: list[JsonObject]
    result: EngineResult | None = None


class RuleViolation(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class GameEngine(Protocol):
    game_id: str
    rules_version: int

    def initial_state(self, options: Mapping[str, Any], rng: Random) -> JsonObject: ...

    def current_actor(self, state: Mapping[str, Any]) -> ActorKind | None: ...

    def public_state(self, state: Mapping[str, Any]) -> JsonObject: ...

    def public_action(
        self,
        state: Mapping[str, Any],
        actor: ActorKind,
        action: Mapping[str, Any],
    ) -> JsonObject: ...

    def apply_action(
        self,
        state: Mapping[str, Any],
        actor: ActorKind,
        action: Mapping[str, Any],
    ) -> Transition: ...


def require_actor(actor: str) -> ActorKind:
    if actor not in ("user", "persona"):
        raise RuleViolation("invalid_actor", "actor must be user or persona")
    return actor


def require_turn(state: Mapping[str, Any], actor: str) -> ActorKind:
    checked_actor = require_actor(actor)
    if state.get("status") != "active":
        raise RuleViolation("game_not_active", "the game is no longer active")
    if state.get("turn") != checked_actor:
        raise RuleViolation("not_actor_turn", f"it is not {checked_actor}'s turn")
    return checked_actor


def require_object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuleViolation("invalid_action", f"{name} must be an object")
    return value


def require_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    code: str = "invalid_action",
) -> None:
    allowed = required | (optional or set())
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        raise RuleViolation(code, f"missing field: {sorted(missing)[0]}")
    if unknown:
        raise RuleViolation(code, f"unknown field: {sorted(unknown)[0]}")


def other_actor(actor: ActorKind) -> ActorKind:
    return "persona" if actor == "user" else "user"
