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


ANSWERS = {"yes", "no", "unknown"}
VERDICTS = {"correct", "incorrect"}
MAX_QUESTIONS = 20


def _require_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleViolation("invalid_text", "text must be a non-empty string")
    return value.strip()


def _result(winner: ActorKind, outcome: str, notable: str) -> EngineResult:
    return {
        "winner": winner,
        "outcome": outcome,
        "score": {
            "user": 1 if winner == "user" else 0,
            "persona": 1 if winner == "persona" else 0,
        },
        "notable_events": [notable],
    }


class TwentyQuestionsEngine:
    game_id = "twenty_questions"
    rules_version = 1

    def initial_state(self, options: Mapping[str, Any], rng: Random) -> JsonObject:
        del rng
        options = require_object(options, "options")
        require_keys(options, required=set(), code="invalid_options")
        return {
            "status": "active",
            "turn": "persona",
            "question_count": 0,
            "max_questions": MAX_QUESTIONS,
            "pending": None,
            "transcript": [],
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
        if actor == "persona":
            return self._apply_persona_action(state, action)
        return self._apply_user_action(state, action)

    def _apply_persona_action(
        self, state: Mapping[str, Any], action: Mapping[str, Any]
    ) -> Transition:
        require_keys(action, required={"kind", "text"})
        kind = action["kind"]
        if kind not in ("question", "guess"):
            raise RuleViolation("invalid_action_kind", "kind must be question or guess")
        text = _require_text(action["text"])
        count = int(state.get("question_count", 0))
        if count >= MAX_QUESTIONS:
            raise RuleViolation("question_limit_reached", "no questions remain")

        ordinal = count + 1
        pending = {"kind": kind, "text": text, "ordinal": ordinal}
        next_state = deepcopy(dict(state))
        next_state.update(
            {
                "turn": "user",
                "question_count": ordinal,
                "pending": pending,
                "transcript": [*list(state.get("transcript", [])), pending.copy()],
            }
        )
        event_type = "question_asked" if kind == "question" else "guess_made"
        return Transition(
            state=next_state,
            events=[{"type": event_type, "text": text, "ordinal": ordinal}],
        )

    def _apply_user_action(
        self, state: Mapping[str, Any], action: Mapping[str, Any]
    ) -> Transition:
        pending = state.get("pending")
        if not isinstance(pending, Mapping) or pending.get("kind") not in (
            "question",
            "guess",
        ):
            raise RuleViolation("missing_pending_prompt", "there is no prompt to answer")

        next_state = deepcopy(dict(state))
        transcript = list(next_state.get("transcript", []))
        ordinal = int(pending["ordinal"])
        result: EngineResult | None = None
        events: list[JsonObject]
        if pending["kind"] == "question":
            require_keys(action, required={"answer"})
            answer = action["answer"]
            if not isinstance(answer, str) or answer not in ANSWERS:
                raise RuleViolation(
                    "invalid_answer", "answer must be yes, no, or unknown"
                )
            transcript[-1] = {**transcript[-1], "answer": answer}
            events = [{"type": "question_answered", "answer": answer, "ordinal": ordinal}]
            if ordinal >= MAX_QUESTIONS:
                next_state.update({"status": "finished", "turn": None, "pending": None})
                result = _result("user", "user_win", "question_limit_reached")
                events.append(
                    {"type": "game_finished", "outcome": "user_win", "winner": "user"}
                )
            else:
                next_state.update({"turn": "persona", "pending": None})
        else:
            require_keys(action, required={"verdict"})
            verdict = action["verdict"]
            if not isinstance(verdict, str) or verdict not in VERDICTS:
                raise RuleViolation(
                    "invalid_verdict", "verdict must be correct or incorrect"
                )
            transcript[-1] = {**transcript[-1], "verdict": verdict}
            events = [{"type": "guess_judged", "verdict": verdict, "ordinal": ordinal}]
            if verdict == "correct":
                next_state.update({"status": "finished", "turn": None, "pending": None})
                result = _result("persona", "persona_win", f"guessed_on_question:{ordinal}")
                events.append(
                    {
                        "type": "game_finished",
                        "outcome": "persona_win",
                        "winner": "persona",
                    }
                )
            elif ordinal >= MAX_QUESTIONS:
                next_state.update({"status": "finished", "turn": None, "pending": None})
                result = _result("user", "user_win", "question_limit_reached")
                events.append(
                    {"type": "game_finished", "outcome": "user_win", "winner": "user"}
                )
            else:
                next_state.update({"turn": "persona", "pending": None})

        next_state["transcript"] = transcript
        return Transition(state=next_state, events=events, result=result)


ENGINE = TwentyQuestionsEngine()
