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
    require_keys,
    require_object,
    require_turn,
)


CHOICES = {"rock", "paper", "scissors"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


class RockPaperScissorsEngine:
    game_id = "rock_paper_scissors"
    rules_version = 1

    def initial_state(self, options: Mapping[str, Any], rng: Random) -> JsonObject:
        del rng
        options = require_object(options, "options")
        require_keys(options, required=set(), code="invalid_options")
        return {
            "status": "active",
            "turn": "user",
            "user_choice": None,
            "persona_choice": None,
        }

    def current_actor(self, state: Mapping[str, Any]) -> ActorKind | None:
        if state.get("status") != "active":
            return None
        turn = state.get("turn")
        return turn if turn in ("user", "persona") else None

    def public_state(self, state: Mapping[str, Any]) -> JsonObject:
        public = deepcopy(dict(state))
        if state.get("status") == "active" and state.get("turn") == "persona":
            public.pop("user_choice", None)
            public["user_choice_committed"] = True
        return public

    def public_action(
        self,
        state: Mapping[str, Any],
        actor: ActorKind,
        action: Mapping[str, Any],
    ) -> JsonObject:
        if (
            actor == "user"
            and state.get("status") == "active"
            and state.get("turn") == "persona"
        ):
            return {"choice_committed": True}
        return deepcopy(dict(action))

    def apply_action(
        self,
        state: Mapping[str, Any],
        actor: ActorKind,
        action: Mapping[str, Any],
    ) -> Transition:
        actor = require_turn(state, actor)
        action = require_object(action, "action")
        require_keys(action, required={"choice"})
        choice = action["choice"]
        if not isinstance(choice, str) or choice not in CHOICES:
            raise RuleViolation(
                "invalid_choice", "choice must be rock, paper, or scissors"
            )

        next_state = deepcopy(dict(state))
        if actor == "user":
            next_state.update({"user_choice": choice, "turn": "persona"})
            return Transition(
                state=next_state,
                events=[{"type": "choice_committed", "actor": "user"}],
            )

        user_choice = state.get("user_choice")
        if user_choice not in CHOICES:
            raise RuleViolation("missing_user_choice", "the user choice has not been committed")
        next_state.update(
            {"persona_choice": choice, "status": "finished", "turn": None}
        )
        if user_choice == choice:
            winner = "draw"
            outcome = "draw"
            score = {"user": 0, "persona": 0}
        elif BEATS[user_choice] == choice:
            winner = "user"
            outcome = "user_win"
            score = {"user": 1, "persona": 0}
        else:
            winner = "persona"
            outcome = "persona_win"
            score = {"user": 0, "persona": 1}
        result: EngineResult = {
            "winner": winner,
            "outcome": outcome,
            "score": score,
            "notable_events": [f"user:{user_choice}", f"persona:{choice}"],
        }
        return Transition(
            state=next_state,
            events=[
                {
                    "type": "choices_revealed",
                    "user_choice": user_choice,
                    "persona_choice": choice,
                },
                {"type": "game_finished", "outcome": outcome, "winner": winner},
            ],
            result=result,
        )


ENGINE = RockPaperScissorsEngine()
