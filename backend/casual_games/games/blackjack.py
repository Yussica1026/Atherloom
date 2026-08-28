from __future__ import annotations

from copy import deepcopy
from random import Random
from typing import Any, Literal, Mapping, Sequence

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


RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("clubs", "diamonds", "hearts", "spades")
DECISIONS = {"hit", "stand"}
Winner = Literal["user", "persona", "draw"]
PUBLIC_STATE_FIELDS = (
    "status",
    "turn",
    "user_hand",
    "persona_hand",
    "user_total",
    "persona_total",
    "user_stood",
    "persona_stood",
    "deck_remaining",
)


def _validated_card(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"rank", "suit"}:
        raise RuleViolation("invalid_state", "a blackjack card is invalid")
    rank = value.get("rank")
    suit = value.get("suit")
    if rank not in RANKS or suit not in SUITS:
        raise RuleViolation("invalid_state", "a blackjack card is invalid")
    return {"rank": str(rank), "suit": str(suit)}


def _validated_cards(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise RuleViolation("invalid_state", f"{field} must be a card list")
    return [_validated_card(card) for card in value]


def hand_total(hand: Sequence[Mapping[str, Any]]) -> int:
    """Return the best blackjack total, counting each ace as 1 or 11."""

    total = 0
    aces = 0
    for raw_card in hand:
        card = _validated_card(raw_card)
        rank = card["rank"]
        if rank == "A":
            total += 1
            aces += 1
        elif rank in {"J", "Q", "K"}:
            total += 10
        else:
            total += int(rank)
    if aces and total + 10 <= 21:
        total += 10
    return total


def _result(
    state: Mapping[str, Any],
    winner: Winner,
    reason: str,
) -> EngineResult:
    if winner not in ("user", "persona", "draw"):
        raise RuleViolation("invalid_state", "blackjack winner is invalid")
    user_total = int(state["user_total"])
    persona_total = int(state["persona_total"])
    user_hand = _validated_cards(state.get("user_hand"), "user_hand")
    persona_hand = _validated_cards(state.get("persona_hand"), "persona_hand")
    outcome: Literal["user_win", "persona_win", "draw"]
    if winner == "draw":
        outcome = "draw"
    elif winner == "user":
        outcome = "user_win"
    else:
        outcome = "persona_win"
    notable_events = [
        f"finish_reason:{reason}",
        f"user_total:{user_total}",
        f"persona_total:{persona_total}",
        f"user_cards:{len(user_hand)}",
        f"persona_cards:{len(persona_hand)}",
    ]
    if user_total == 21 and len(user_hand) == 2:
        notable_events.append("natural_blackjack:user")
    if persona_total == 21 and len(persona_hand) == 2:
        notable_events.append("natural_blackjack:persona")
    return {
        "winner": winner,
        "outcome": outcome,
        "score": {"user": user_total, "persona": persona_total},
        "notable_events": notable_events,
    }


def _finish(
    state: JsonObject,
    events: list[JsonObject],
    *,
    winner: Winner,
    reason: str,
) -> Transition:
    state.update({"status": "finished", "turn": None})
    result = _result(state, winner, reason)
    events.append(
        {
            "type": "game_finished",
            "outcome": result["outcome"],
            "winner": result["winner"],
            "reason": reason,
        }
    )
    return Transition(state=state, events=events, result=result)


def _finish_by_totals(
    state: JsonObject,
    events: list[JsonObject],
    *,
    reason: str,
) -> Transition:
    user_total = int(state["user_total"])
    persona_total = int(state["persona_total"])
    if user_total > persona_total:
        winner: Winner = "user"
    elif persona_total > user_total:
        winner = "persona"
    else:
        winner = "draw"
    return _finish(state, events, winner=winner, reason=reason)


class BlackjackEngine:
    game_id = "blackjack"
    rules_version = 1

    def initial_state(self, options: Mapping[str, Any], rng: Random) -> JsonObject:
        options = require_object(options, "options")
        require_keys(options, required=set(), code="invalid_options")

        deck = [
            {"rank": rank, "suit": suit}
            for suit in SUITS
            for rank in RANKS
        ]
        rng.shuffle(deck)
        user_hand: list[dict[str, str]] = []
        persona_hand: list[dict[str, str]] = []
        for _ in range(2):
            user_hand.append(deck.pop())
            persona_hand.append(deck.pop())
        return {
            "status": "active",
            "turn": "user",
            "user_hand": user_hand,
            "persona_hand": persona_hand,
            "user_total": hand_total(user_hand),
            "persona_total": hand_total(persona_hand),
            "user_stood": False,
            "persona_stood": False,
            "deck": deck,
            "deck_remaining": len(deck),
        }

    def current_actor(self, state: Mapping[str, Any]) -> ActorKind | None:
        if state.get("status") != "active":
            return None
        turn = state.get("turn")
        return turn if turn in ("user", "persona") else None

    def public_state(self, state: Mapping[str, Any]) -> JsonObject:
        return {
            field: deepcopy(state[field])
            for field in PUBLIC_STATE_FIELDS
            if field in state
        }

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
        require_keys(action, required={"decision"})
        decision = action["decision"]
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise RuleViolation("invalid_decision", "decision must be hit or stand")

        user_hand = _validated_cards(state.get("user_hand"), "user_hand")
        persona_hand = _validated_cards(state.get("persona_hand"), "persona_hand")
        deck = _validated_cards(state.get("deck"), "deck")
        user_total = hand_total(user_hand)
        persona_total = hand_total(persona_hand)
        if state.get("user_total") != user_total or state.get("persona_total") != persona_total:
            raise RuleViolation("invalid_state", "blackjack hand total is inconsistent")
        if state.get("deck_remaining") != len(deck):
            raise RuleViolation("invalid_state", "blackjack deck count is inconsistent")
        if not isinstance(state.get("user_stood"), bool) or not isinstance(
            state.get("persona_stood"), bool
        ):
            raise RuleViolation("invalid_state", "blackjack stood state is invalid")

        stood_key = f"{actor}_stood"
        other = other_actor(actor)
        other_stood_key = f"{other}_stood"
        hand_key = f"{actor}_hand"
        total_key = f"{actor}_total"
        if state[stood_key]:
            raise RuleViolation("actor_stood", f"{actor} has already stood")

        next_state = deepcopy(dict(state))
        next_state["user_hand"] = user_hand
        next_state["persona_hand"] = persona_hand
        next_state["deck"] = deck
        events: list[JsonObject] = []

        if decision == "stand":
            next_state[stood_key] = True
            events.append({"type": "hand_stood", "actor": actor})
            if next_state[other_stood_key]:
                return _finish_by_totals(
                    next_state,
                    events,
                    reason="both_stood",
                )
            next_state["turn"] = other
            return Transition(state=next_state, events=events)

        actor_total = int(next_state[total_key])
        if actor_total >= 21:
            raise RuleViolation("cannot_hit", "a hand with 21 or more cannot hit")
        if not deck:
            raise RuleViolation("deck_empty", "the blackjack deck is empty")

        card = deck.pop()
        actor_hand = list(next_state[hand_key])
        actor_hand.append(card)
        actor_total = hand_total(actor_hand)
        next_state.update(
            {
                hand_key: actor_hand,
                total_key: actor_total,
                "deck": deck,
                "deck_remaining": len(deck),
            }
        )
        events.append(
            {
                "type": "card_drawn",
                "actor": actor,
                "card": deepcopy(card),
                "deck_remaining": len(deck),
            }
        )

        if actor_total > 21:
            return _finish(
                next_state,
                events,
                winner=other,
                reason=f"{actor}_bust",
            )

        if actor_total == 21:
            next_state[stood_key] = True
            events.append(
                {"type": "automatic_stand", "actor": actor, "total": actor_total}
            )
            if next_state[other_stood_key]:
                return _finish_by_totals(
                    next_state,
                    events,
                    reason="both_stood",
                )

        next_state["turn"] = actor if next_state[other_stood_key] else other
        return Transition(state=next_state, events=events)


ENGINE = BlackjackEngine()
