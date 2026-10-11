from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import ValidationError

from backend.game.gm_port import (
    GameMasterConfigurationError,
    GameMasterPort,
    GameMasterResponseError,
    GameMasterTransportError,
)
from backend.game.models import (
    BranchCreateIn,
    PlayerActionIn,
    SaveCreateIn,
    SessionCreateIn,
    WorldCreateIn,
)
from backend.game.repository import GameConflict, GameDataError, GameNotFound, GameRepository
from backend.game.rules import RuleViolation
from backend.game.schema import DbPath
from backend.game.service import LongWorldTurnService


GameMasterFactory = Callable[[str], GameMasterPort | None]


class _LazyGameMaster:
    """Resolve provider infrastructure only after the service preflight misses."""

    def __init__(
        self,
        factory: GameMasterFactory | None,
        provider_id: str,
    ):
        self.factory = factory
        self.provider_id = provider_id

    async def propose_turn(self, context: Any) -> object:
        if self.factory is None:
            raise GameMasterConfigurationError("当前运行环境尚未配置长期世界 GM")
        game_master = self.factory(self.provider_id)
        if game_master is None:
            raise GameMasterConfigurationError("GM Provider 不存在、已停用或配置无效")
        return await game_master.propose_turn(context)


def create_router(
    db_path: DbPath,
    ai_character_resolver: Callable[[str, str], object | None] | None = None,
    game_master_factory: GameMasterFactory | None = None,
) -> tuple[APIRouter, Any]:
    router = APIRouter(tags=["long-world"])
    repository = GameRepository(db_path, ai_character_resolver=ai_character_resolver)

    def invoke(method: Any, *args: Any) -> Any:
        try:
            return method(*args)
        except GameNotFound as error:
            raise HTTPException(404, str(error)) from error
        except GameConflict as error:
            raise HTTPException(409, str(error)) from error
        except RuleViolation as error:
            raise HTTPException(
                422,
                {"code": error.code, "message": error.message},
            ) from error
        except GameDataError as error:
            raise HTTPException(500, f"长期世界数据一致性错误：{error}") from error

    async def invoke_async(method: Any, *args: Any) -> Any:
        try:
            return await method(*args)
        except GameNotFound as error:
            raise HTTPException(404, str(error)) from error
        except GameConflict as error:
            raise HTTPException(409, str(error)) from error
        except RuleViolation as error:
            raise HTTPException(
                422,
                {"code": error.code, "message": error.message},
            ) from error
        except GameMasterConfigurationError as error:
            raise HTTPException(422, str(error)) from error
        except (GameMasterTransportError, GameMasterResponseError) as error:
            raise HTTPException(502, str(error)) from error
        except ValidationError as error:
            raise HTTPException(502, "GM 返回的候选回合不符合严格 schema") from error
        except GameDataError as error:
            raise HTTPException(500, f"长期世界数据一致性错误：{error}") from error

    def persistent_operation_key(
        header_key: str | None,
        query_key: str | None,
    ) -> str | None:
        if header_key is not None and query_key is not None and header_key != query_key:
            raise HTTPException(
                422,
                "Idempotency-Key header 与 idempotency_key query 必须一致",
            )
        return header_key if header_key is not None else query_key

    @router.post("/api/game-worlds", status_code=201)
    def create_world(
        body: WorldCreateIn,
        idempotency_key_header: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=1,
            max_length=160,
        ),
        idempotency_key: str | None = Query(
            default=None,
            min_length=1,
            max_length=160,
        ),
    ) -> dict[str, Any]:
        return invoke(
            repository.create_world,
            body,
            persistent_operation_key(idempotency_key_header, idempotency_key),
        )

    @router.get("/api/game-worlds")
    def list_worlds() -> dict[str, Any]:
        return {"worlds": invoke(repository.list_worlds)}

    @router.get("/api/game-worlds/{world_id}")
    def get_world(world_id: str) -> dict[str, Any]:
        return invoke(repository.get_world, world_id)

    @router.post("/api/game-sessions", status_code=201)
    def create_session(
        body: SessionCreateIn,
        idempotency_key_header: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=1,
            max_length=160,
        ),
        idempotency_key: str | None = Query(
            default=None,
            min_length=1,
            max_length=160,
        ),
    ) -> dict[str, Any]:
        return invoke(
            repository.create_session,
            body,
            persistent_operation_key(idempotency_key_header, idempotency_key),
        )

    @router.get("/api/game-sessions")
    def list_sessions(
        world_id: str | None = Query(default=None, min_length=1, max_length=120),
    ) -> dict[str, Any]:
        return {"sessions": invoke(repository.list_sessions, world_id)}

    @router.get("/api/game-sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        return invoke(repository.get_session, session_id)

    @router.get("/api/game-sessions/{session_id}/state")
    def get_state(session_id: str) -> dict[str, Any]:
        return invoke(repository.get_session, session_id)["state"]

    @router.post("/api/game-sessions/{session_id}/actions")
    def commit_player_action(session_id: str, body: PlayerActionIn) -> dict[str, Any]:
        return invoke(repository.commit_player_action, session_id, body)

    @router.post("/api/game-sessions/{session_id}/gm-turns")
    async def commit_gm_player_turn(
        session_id: str,
        body: PlayerActionIn,
        provider_id: str = Query(min_length=1, max_length=120),
    ) -> dict[str, Any]:
        service = LongWorldTurnService(
            repository,
            _LazyGameMaster(game_master_factory, provider_id),
        )
        committed = await invoke_async(service.run_player_turn, session_id, body)
        # A key may already have been committed through deterministic /actions.
        # Keep the GM route response shape stable without changing that old route.
        committed.setdefault("narration", None)
        return committed

    @router.get("/api/game-sessions/{session_id}/turns")
    def list_turns(session_id: str) -> dict[str, Any]:
        return {"turns": invoke(repository.list_turns, session_id)}

    @router.get("/api/game-sessions/{session_id}/events")
    def list_events(session_id: str) -> dict[str, Any]:
        return {"events": invoke(repository.list_events, session_id)}

    @router.get("/api/game-sessions/{session_id}/replay")
    def replay_session(
        session_id: str,
        revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        return invoke(repository.replay_result, session_id, revision)

    @router.post("/api/game-sessions/{session_id}/saves", status_code=201)
    def create_save(
        session_id: str,
        body: SaveCreateIn,
        idempotency_key_header: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=1,
            max_length=160,
        ),
        idempotency_key: str | None = Query(
            default=None,
            min_length=1,
            max_length=160,
        ),
    ) -> dict[str, Any]:
        return invoke(
            repository.create_save,
            session_id,
            body,
            persistent_operation_key(idempotency_key_header, idempotency_key),
        )

    @router.get("/api/game-sessions/{session_id}/saves")
    def list_saves(session_id: str) -> dict[str, Any]:
        return {"saves": invoke(repository.list_saves, session_id)}

    @router.post(
        "/api/game-sessions/{session_id}/saves/{save_id}/branch",
        status_code=201,
    )
    def branch_from_save(
        session_id: str,
        save_id: str,
        body: BranchCreateIn,
        idempotency_key_header: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=1,
            max_length=160,
        ),
        idempotency_key: str | None = Query(
            default=None,
            min_length=1,
            max_length=160,
        ),
    ) -> dict[str, Any]:
        return invoke(
            repository.branch_from_save,
            session_id,
            save_id,
            body,
            persistent_operation_key(idempotency_key_header, idempotency_key),
        )

    return router, repository.init
