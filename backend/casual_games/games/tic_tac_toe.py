from __future__ import annotations

from copy import deepcopy
from random import Random
from typing import Any, Mapping

from .base import (
    ActorKind,
    EngineResult,
    JsonObject,
    RuleViolation,
    Transition,
    other_actor,
    require_keys,
    require_object,
    require_turn,
)


WINNING_LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)


class TicTacToeEngine:
    game_id = "tic_tac_toe"
    rules_version = 1

    def initial_state(self, options: Mapping[str, Any], rng: Random) -> JsonObject:
        del rng
        options = require_object(options, "options")
        require_keys(options, required=set(), optional={"first_actor"}, code="invalid_options")
        first_actor = options.get("first_actor", "user")
        if first_actor not in ("user", "persona"):
            raise RuleViolation("invalid_options", "first_actor must be user or persona")
        return {
            "status": "active",
            "turn": first_actor,
            "board": [None] * 9,
            "move_count": 0,
        }

    def current_actor(self, state: Mapping[str, Any]) -> ActorKind | None:
        if state.get("status") != "active":
            return None
        turn = state.get("turn")
        return turn if turn in ("user", "persona") else None

    def public_state(self, state: Mapping[str, Any]) -> JsonObject:
        return deepcopy(dict(state))

    def public_action(
        self,
        state: Mapping[str, Any],
        actor: ActorKind,
        action: Mapping[str, Any],
    ) -> JsonObject:
        del state, actor
        return deepcopy(dict(action))

    def apply_action(
        self,
        state: Mapping[str, Any],
        actor: ActorKind,
        action: Mapping[str, Any],
    ) -> Transition:
        actor = require_turn(state, actor)
        action = require_object(action, "action")
        require_keys(action, required={"position"})
        position = action["position"]
        if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= 8:
            raise RuleViolation("invalid_position", "position must be an integer from 0 to 8")

        board = list(state["board"])
        if board[position] is not None:
            raise RuleViolation("position_occupied", "the selected position is occupied")

        mark = "X" if actor == "user" else "O"
        board[position] = mark
        move_count = int(state.get("move_count", 0)) + 1
        next_state = deepcopy(dict(state))
        next_state.update({"board": board, "move_count": move_count})
        events: list[JsonObject] = [
            {"type": "mark_placed", "actor": actor, "position": position, "mark": mark}
        ]

        winning_line = next(
            (line for line in WINNING_LINES if all(board[index] == mark for index in line)),
            None,
        )
        result: EngineResult | None = None
        if winning_line is not None:
            next_state.update({"status": "finished", "turn": None})
            result = {
                "winner": actor,
                "outcome": f"{actor}_win",
                "score": {
                    "user": 1 if actor == "user" else 0,
                    "persona": 1 if actor == "persona" else 0,
                },
                "notable_events": ["winning_line:" + ",".join(map(str, winning_line))],
            }
            events.append(
                {"type": "game_finished", "outcome": result["outcome"], "winner": actor}
            )
        elif move_count == 9:
            next_state.update({"status": "finished", "turn": None})
            result = {
                "winner": "draw",
                "outcome": "draw",
                "score": {"user": 0, "persona": 0},
                "notable_events": ["board_filled"],
            }
            events.append({"type": "game_finished", "outcome": "draw", "winner": "draw"})
        else:
            next_state["turn"] = other_actor(actor)

        return Transition(state=next_state, events=events, result=result)


ENGINE = TicTacToeEngine()
