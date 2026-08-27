from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from backend.casual_games.games import (
    BullsAndCowsEngine,
    GameEngine,
    RockPaperScissorsEngine,
    TicTacToeEngine,
    TwentyQuestionsEngine,
)


class UnknownGame(ValueError):
    pass


class UnsupportedRulesVersion(ValueError):
    pass


@dataclass(frozen=True)
class GameRegistration:
    game_id: str
    name: str
    engine: GameEngine
    options_schema: Mapping[str, Any]
    user_action_schema: Mapping[str, Any]
    persona_action_schema: Mapping[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.game_id,
            "name": self.name,
            "rules_version": self.engine.rules_version,
            "options_schema": dict(self.options_schema),
            "user_action_schema": dict(self.user_action_schema),
            "persona_action_schema": dict(self.persona_action_schema),
        }


REGISTRATIONS = (
    GameRegistration(
        game_id="tic_tac_toe",
        name="井字棋",
        engine=TicTacToeEngine(),
        options_schema={
            "type": "object",
            "properties": {"first_actor": {"enum": ["user", "persona"]}},
            "additionalProperties": False,
        },
        user_action_schema={
            "type": "object",
            "required": ["position"],
            "properties": {"position": {"type": "integer", "minimum": 0, "maximum": 8}},
            "additionalProperties": False,
        },
        persona_action_schema={
            "type": "object",
            "required": ["position"],
            "properties": {"position": {"type": "integer", "minimum": 0, "maximum": 8}},
            "additionalProperties": False,
        },
    ),
    GameRegistration(
        game_id="rock_paper_scissors",
        name="猜拳",
        engine=RockPaperScissorsEngine(),
        options_schema={"type": "object", "additionalProperties": False},
        user_action_schema={
            "type": "object",
            "required": ["choice"],
            "properties": {"choice": {"enum": ["rock", "paper", "scissors"]}},
            "additionalProperties": False,
        },
        persona_action_schema={
            "type": "object",
            "required": ["choice"],
            "properties": {"choice": {"enum": ["rock", "paper", "scissors"]}},
            "additionalProperties": False,
        },
    ),
    GameRegistration(
        game_id="bulls_and_cows",
        name="猜数字",
        engine=BullsAndCowsEngine(),
        options_schema={
            "type": "object",
            "required": ["user_secret"],
            "properties": {
                "user_secret": {
                    "type": "string",
                    "pattern": r"^(?!.*([0-9]).*\1)[0-9]{4}$",
                }
            },
            "additionalProperties": False,
        },
        user_action_schema={
            "type": "object",
            "required": ["guess"],
            "properties": {
                "guess": {
                    "type": "string",
                    "pattern": r"^(?!.*([0-9]).*\1)[0-9]{4}$",
                }
            },
            "additionalProperties": False,
        },
        persona_action_schema={
            "type": "object",
            "required": ["guess"],
            "properties": {
                "guess": {
                    "type": "string",
                    "pattern": r"^(?!.*([0-9]).*\1)[0-9]{4}$",
                }
            },
            "additionalProperties": False,
        },
    ),
    GameRegistration(
        game_id="twenty_questions",
        name="二十问",
        engine=TwentyQuestionsEngine(),
        options_schema={"type": "object", "additionalProperties": False},
        user_action_schema={
            "oneOf": [
                {
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"enum": ["yes", "no", "unknown"]}},
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "required": ["verdict"],
                    "properties": {"verdict": {"enum": ["correct", "incorrect"]}},
                    "additionalProperties": False,
                },
            ]
        },
        persona_action_schema={
            "type": "object",
            "required": ["kind", "text"],
            "properties": {
                "kind": {"enum": ["question", "guess"]},
                "text": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
    ),
)


_BY_ID = {registration.game_id: registration for registration in REGISTRATIONS}


def list_games() -> list[dict[str, Any]]:
    return [registration.public_dict() for registration in REGISTRATIONS]


def get_registration(game_id: str) -> GameRegistration:
    try:
        return _BY_ID[game_id]
    except KeyError as error:
        raise UnknownGame(f"unsupported casual game: {game_id}") from error


def get_engine(game_id: str, rules_version: int | None = None) -> GameEngine:
    engine = get_registration(game_id).engine
    if rules_version is not None and engine.rules_version != rules_version:
        raise UnsupportedRulesVersion(
            f"unsupported rules version for {game_id}: {rules_version}"
        )
    return engine
