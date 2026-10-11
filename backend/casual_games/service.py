from __future__ import annotations

import random
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.casual_games.games import ActorKind, RuleViolation
from backend.casual_games.models import (
    ActionCommitIn,
    CasualGameParticipant,
    CasualGameResult,
    CasualGameSession,
    SessionCreateIn,
    SessionStatus,
)
from backend.casual_games.registry import get_engine
from backend.casual_games.repository import (
    CasualGameConflict,
    CasualGameRepository,
    now_iso,
)


@dataclass(frozen=True)
class ConversationBinding:
    conversation_id: str
    persona_id: str | None
    player_id: str = "local_user"


ConversationResolver = Callable[
    [str], ConversationBinding | Mapping[str, Any] | None
]
RandomFactory = Callable[[], random.Random]


def _default_random_factory() -> random.Random:
    return random.Random(secrets.randbits(128))


class CasualGameService:
    def __init__(
        self,
        repository: CasualGameRepository,
        conversation_resolver: ConversationResolver,
        *,
        random_factory: RandomFactory | None = None,
    ):
        self.repository = repository
        self.conversation_resolver = conversation_resolver
        self.random_factory = random_factory or _default_random_factory

    def create_session(
        self,
        body: SessionCreateIn,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        binding = self._resolve_conversation(body.conversation_id)
        engine = get_engine(body.game_id)
        state = engine.initial_state(body.options, self.random_factory())
        session = self.repository.create_session(
            game_id=engine.game_id,
            rules_version=engine.rules_version,
            conversation_id=binding.conversation_id,
            persona_id=binding.persona_id,
            player_id=binding.player_id,
            state=state,
            idempotency_key=idempotency_key,
            creation_request=body.model_dump(mode="json"),
        )
        return self.public_session(session)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.public_session(self.repository.get_session(session_id))

    def list_sessions(
        self,
        *,
        conversation_id: str | None = None,
        persona_id: str | None = None,
        status: SessionStatus | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            self.public_session(session)
            for session in self.repository.list_sessions(
                conversation_id=conversation_id,
                persona_id=persona_id,
                status=status,
                limit=limit,
            )
        ]

    def commit_user_action(
        self,
        session_id: str,
        body: ActionCommitIn,
    ) -> dict[str, Any]:
        session = self.repository.get_session(session_id)
        return self._commit_action(session, "user", session.player_id, body)

    def commit_persona_action(
        self,
        session_id: str,
        body: ActionCommitIn,
    ) -> dict[str, Any]:
        session = self.repository.get_session(session_id)
        return self._commit_action(session, "persona", session.persona_id, body)

    def abandon_session(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self.public_session(
            self.repository.abandon_session(
                session_id,
                expected_revision=expected_revision,
            )
        )

    def list_public_history(self, session_id: str) -> list[dict[str, Any]]:
        session = self.repository.get_session(session_id)
        engine = get_engine(session.game_id, session.rules_version)
        return [
            {
                "id": entry.id,
                "session_id": entry.session_id,
                "revision": entry.revision,
                "actor_kind": entry.actor_kind,
                "actor_id": entry.actor_id,
                "action": engine.public_action(
                    session.state,
                    entry.actor_kind,
                    entry.action,
                ),
                "events": entry.events,
                "created_at": entry.created_at,
            }
            for entry in self.repository.list_history(session_id)
        ]

    def public_session(self, session: CasualGameSession) -> dict[str, Any]:
        engine = get_engine(session.game_id, session.rules_version)
        state = engine.public_state(session.state)
        current_actor = engine.current_actor(session.state)
        if session.status == "abandoned":
            state = {**state, "status": "abandoned", "turn": None}
            current_actor = None
        return {
            "id": session.id,
            "game_id": session.game_id,
            "rules_version": session.rules_version,
            "conversation_id": session.conversation_id,
            "persona_id": session.persona_id,
            "player_id": session.player_id,
            "state": state,
            "current_actor": current_actor,
            "status": session.status,
            "revision": session.revision,
            "result_id": session.result_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "finished_at": session.finished_at,
        }

    def _resolve_conversation(self, conversation_id: str) -> ConversationBinding:
        resolved = self.conversation_resolver(conversation_id)
        if resolved is None:
            raise CasualGameConflict("当前聊天不存在，不能创建休闲游戏")
        if isinstance(resolved, ConversationBinding):
            binding = resolved
        else:
            binding = ConversationBinding(
                conversation_id=str(resolved.get("conversation_id") or conversation_id),
                persona_id=(
                    str(resolved["persona_id"]).strip()
                    if resolved.get("persona_id") is not None
                    else None
                ),
                player_id=str(resolved.get("player_id") or "local_user").strip(),
            )
        if binding.conversation_id != conversation_id:
            raise CasualGameConflict("聊天绑定与请求的 conversation_id 不一致")
        if binding.persona_id is None or not binding.persona_id.strip():
            raise CasualGameConflict("当前聊天尚未绑定 Persona，不能创建休闲游戏")
        if not binding.player_id:
            raise CasualGameConflict("当前本地 Player 身份无效")
        return ConversationBinding(
            conversation_id=binding.conversation_id,
            persona_id=binding.persona_id.strip(),
            player_id=binding.player_id,
        )

    def _commit_action(
        self,
        session: CasualGameSession,
        actor_kind: ActorKind,
        actor_id: str,
        body: ActionCommitIn,
    ) -> dict[str, Any]:
        replay = self.repository.find_idempotent_response(
            session.id,
            actor_kind,
            actor_id,
            body,
        )
        if replay is not None:
            return {**replay, "idempotent_replay": True}
        if session.status != "active":
            raise CasualGameConflict("休闲游戏 Session 已结束")
        current_binding = self._resolve_conversation(session.conversation_id)
        if current_binding.persona_id != session.persona_id:
            raise CasualGameConflict(
                "当前聊天已切换 Persona，不能用另一个 Persona 继续这局游戏"
            )
        if body.expected_revision != session.revision:
            raise CasualGameConflict(
                f"revision 冲突：当前为 {session.revision}，请求为 {body.expected_revision}"
            )

        engine = get_engine(session.game_id, session.rules_version)
        transition = engine.apply_action(session.state, actor_kind, body.action)
        next_revision = session.revision + 1
        result = self._build_result(session, transition.result) if transition.result else None
        status: SessionStatus = "finished" if result is not None else "active"
        response = {
            "session_id": session.id,
            "game_id": session.game_id,
            "revision": next_revision,
            "status": status,
            "state": engine.public_state(transition.state),
            "current_actor": engine.current_actor(transition.state),
            "events": transition.events,
            "result": result.model_dump(mode="json") if result is not None else None,
            "result_id": None,
        }
        committed = self.repository.commit_action(
            session.id,
            actor_kind,
            actor_id,
            body,
            next_state=transition.state,
            events=transition.events,
            response=response,
            result=result,
        )
        return {**committed.response, "idempotent_replay": committed.idempotent_replay}

    @staticmethod
    def _build_result(
        session: CasualGameSession,
        engine_result: Mapping[str, Any],
    ) -> CasualGameResult:
        finished_at = now_iso()
        outcome = engine_result.get("outcome")
        score = engine_result.get("score")
        notable_events = engine_result.get("notable_events", [])
        if not isinstance(outcome, str) or not outcome:
            raise RuleViolation("invalid_engine_result", "engine result is missing outcome")
        if not isinstance(score, dict) or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in score.values()
        ):
            raise RuleViolation("invalid_engine_result", "engine result score is invalid")
        if not isinstance(notable_events, list) or any(
            not isinstance(value, str) for value in notable_events
        ):
            raise RuleViolation(
                "invalid_engine_result",
                "engine result notable_events are invalid",
            )
        return CasualGameResult(
            game_id=session.game_id,
            session_id=session.id,
            outcome=outcome,
            participants=[
                CasualGameParticipant(kind="user", id=session.player_id),
                CasualGameParticipant(kind="persona", id=session.persona_id),
            ],
            score=score,
            notable_events=notable_events,
            started_at=session.created_at,
            finished_at=finished_at,
        )
