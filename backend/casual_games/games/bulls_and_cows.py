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


def _require_number(value: Any, field: str, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 4
        or not value.isascii()
        or not value.isdigit()
        or len(set(value)) != 4
    ):
        raise RuleViolation(code, f"{field} must contain four distinct ASCII digits")
    return value


def _score_guess(secret: str, guess: str) -> tuple[int, int]:
    bulls = sum(left == right for left, right in zip(secret, guess))
    cows = len(set(secret) & set(guess)) - bulls
    return bulls, cows


class BullsAndCowsEngine:
    game_id = "bulls_and_cows"
    rules_version = 1

    def initial_state(self, options: Mapping[str, Any], rng: Random) -> JsonObject:
        options = require_object(options, "options")
        require_keys(options, required={"user_secret"}, code="invalid_options")
        user_secret = _require_number(
            options["user_secret"], "user_secret", code="invalid_options"
        )
        persona_secret = "".join(rng.sample("0123456789", 4))
        return {
            "status": "active",
            "turn": "user",
            "round": 1,
            "user_secret": user_secret,
            "persona_secret": persona_secret,
            "history": [],
        }

    def current_actor(self, state: Mapping[str, Any]) -> ActorKind | None:
        if state.get("status") != "active":
            return None
        turn = state.get("turn")
        return turn if turn in ("user", "persona") else None

    def public_state(self, state: Mapping[str, Any]) -> JsonObject:
        public = deepcopy(dict(state))
        public.pop("user_secret", None)
        public.pop("persona_secret", None)
        return public

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
        require_keys(action, required={"guess"})
        guess = _require_number(action["guess"], "guess", code="invalid_guess")
        secret_key = "persona_secret" if actor == "user" else "user_secret"
        secret = state.get(secret_key)
        if not isinstance(secret, str):
            raise RuleViolation("invalid_state", "the game secret is missing")

        bulls, cows = _score_guess(secret, guess)
        round_number = int(state.get("round", 1))
        entry = {
            "actor": actor,
            "guess": guess,
            "bulls": bulls,
            "cows": cows,
            "round": round_number,
        }
        next_state = deepcopy(dict(state))
        next_state["history"] = [*list(state.get("history", [])), entry]
        events: list[JsonObject] = [{"type": "guess_scored", **entry}]

        result: EngineResult | None = None
        if bulls == 4:
            next_state.update({"status": "finished", "turn": None})
            result = {
                "winner": actor,
                "outcome": f"{actor}_win",
                "score": {
                    "user": 1 if actor == "user" else 0,
                    "persona": 1 if actor == "persona" else 0,
                },
                "notable_events": [f"solved_in_round:{round_number}"],
            }
            events.append(
                {"type": "game_finished", "outcome": result["outcome"], "winner": actor}
            )
        else:
            next_state["turn"] = other_actor(actor)
            if actor == "persona":
                next_state["round"] = round_number + 1

        return Transition(state=next_state, events=events, result=result)


ENGINE = BullsAndCowsEngine()
