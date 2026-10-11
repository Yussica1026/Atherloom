from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from backend.casual_games.games import RuleViolation
from backend.casual_games.models import (
    ActionCommitIn,
    BehaviorConfigUpdate,
    SessionCreateIn,
    SessionStatus,
)
from backend.casual_games.registry import (
    UnknownGame,
    UnsupportedRulesVersion,
    get_registration,
    list_games,
)
from backend.casual_games.repository import (
    CasualGameConflict,
    CasualGameDataError,
    CasualGameNotFound,
    CasualGameRepository,
)
from backend.casual_games.schema import DbPath
from backend.casual_games.service import (
    CasualGameService,
    ConversationResolver,
    RandomFactory,
)


def create_router(
    db_path: DbPath,
    conversation_resolver: ConversationResolver,
    *,
    persona_exists: Callable[[str], bool] | None = None,
    random_factory: RandomFactory | None = None,
    expose_persona_action_candidates: bool = False,
) -> tuple[APIRouter, Any]:
    router = APIRouter(tags=["casual-games"])
    repository = CasualGameRepository(db_path)
    service = CasualGameService(
        repository,
        conversation_resolver,
        random_factory=random_factory,
    )

    def invoke(method: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except CasualGameNotFound as error:
            raise HTTPException(404, str(error)) from error
        except UnknownGame as error:
            raise HTTPException(404, str(error)) from error
        except UnsupportedRulesVersion as error:
            raise HTTPException(409, str(error)) from error
        except CasualGameConflict as error:
            raise HTTPException(409, str(error)) from error
        except RuleViolation as error:
            raise HTTPException(
                422,
                {"code": error.code, "message": error.message},
            ) from error
        except CasualGameDataError as error:
            raise HTTPException(500, f"休闲游戏数据一致性错误：{error}") from error

    def require_persona(persona_id: str) -> None:
        if persona_exists is not None and not persona_exists(persona_id):
            raise HTTPException(404, "Persona 不存在")

    def require_game(game_id: str) -> None:
        invoke(get_registration, game_id)

    @router.get("/api/casual-games")
    def catalog() -> dict[str, Any]:
        return {"games": list_games()}

    @router.post("/api/casual-games/sessions", status_code=201)
    def create_session(
        body: SessionCreateIn,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=1,
            max_length=160,
        ),
    ) -> dict[str, Any]:
        return invoke(service.create_session, body, idempotency_key)

    @router.get("/api/casual-games/sessions")
    def list_sessions(
        conversation_id: str | None = Query(default=None, min_length=1, max_length=160),
        persona_id: str | None = Query(default=None, min_length=1, max_length=160),
        status: SessionStatus | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        return {
            "sessions": invoke(
                service.list_sessions,
                conversation_id=conversation_id,
                persona_id=persona_id,
                status=status,
                limit=limit,
            )
        }

    @router.get("/api/casual-games/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        return invoke(service.get_session, session_id)

    @router.post("/api/casual-games/sessions/{session_id}/actions")
    def commit_user_action(
        session_id: str,
        body: ActionCommitIn,
    ) -> dict[str, Any]:
        return invoke(service.commit_user_action, session_id, body)

    if expose_persona_action_candidates:
        # Pure engine harness for backend tests. The production app leaves this
        # disabled; real Persona moves enter through the model-backed endpoint.
        @router.post("/api/casual-games/sessions/{session_id}/persona-actions")
        def commit_persona_action_candidate(
            session_id: str,
            body: ActionCommitIn,
        ) -> dict[str, Any]:
            return invoke(service.commit_persona_action, session_id, body)

    @router.get("/api/casual-games/sessions/{session_id}/history")
    def list_history(session_id: str) -> dict[str, Any]:
        return {"history": invoke(service.list_public_history, session_id)}

    @router.get("/api/casual-games/sessions/{session_id}/result")
    def get_result(session_id: str) -> dict[str, Any]:
        result = invoke(repository.get_result, session_id)
        if result is None:
            raise HTTPException(404, "休闲游戏尚未产生结果")
        return result.model_dump(mode="json")

    @router.get("/api/casual-games/results/{result_id}")
    def get_result_by_id(result_id: str) -> dict[str, Any]:
        return invoke(repository.get_result_by_id, result_id).model_dump(mode="json")

    @router.post("/api/casual-games/sessions/{session_id}/abandon")
    def abandon_session(
        session_id: str,
        expected_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        return invoke(
            service.abandon_session,
            session_id,
            expected_revision=expected_revision,
        )

    @router.get("/api/casual-games/behavior-configs/{persona_id}")
    def list_behavior_configs(persona_id: str) -> dict[str, Any]:
        require_persona(persona_id)
        configs = invoke(repository.list_behavior_configs, persona_id)
        return {"configs": [config.model_dump(mode="json") for config in configs]}

    @router.get("/api/casual-games/behavior-configs/{persona_id}/{game_id}")
    def get_behavior_config(persona_id: str, game_id: str) -> dict[str, Any]:
        require_persona(persona_id)
        require_game(game_id)
        return invoke(
            repository.get_behavior_config,
            persona_id,
            game_id,
        ).model_dump(mode="json")

    @router.put("/api/casual-games/behavior-configs/{persona_id}/{game_id}")
    def update_behavior_config(
        persona_id: str,
        game_id: str,
        body: BehaviorConfigUpdate,
    ) -> dict[str, Any]:
        require_persona(persona_id)
        require_game(game_id)
        return invoke(
            repository.upsert_behavior_config,
            persona_id,
            game_id,
            body,
        ).model_dump(mode="json")

    @router.delete("/api/casual-games/behavior-configs/{persona_id}/{game_id}")
    def delete_behavior_config(persona_id: str, game_id: str) -> dict[str, bool]:
        require_persona(persona_id)
        require_game(game_id)
        return {
            "deleted": invoke(
                repository.delete_behavior_config,
                persona_id,
                game_id,
            )
        }

    return router, repository.init_schema
