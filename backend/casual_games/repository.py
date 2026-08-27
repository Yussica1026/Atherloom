from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

from .models import (
    ActionCommitIn,
    ActorKind,
    BehaviorConfig,
    BehaviorConfigUpdate,
    CasualGameHistoryEntry,
    CasualGameResult,
    CasualGameResultRecord,
    CasualGameSession,
    PersonaTurnIn,
    SessionStatus,
)
from .schema import DbPath, connect, init_schema


class CasualGameNotFound(LookupError):
    pass


class CasualGameConflict(RuntimeError):
    pass


class CasualGameDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommitOutcome:
    response: dict[str, Any]
    idempotent_replay: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise CasualGameDataError(f"{label} 不是有效 JSON") from error
    if not isinstance(value, dict):
        raise CasualGameDataError(f"{label} 必须是 JSON object")
    return value


def _json_object_list(raw: str, label: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise CasualGameDataError(f"{label} 不是有效 JSON") from error
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CasualGameDataError(f"{label} 必须是 JSON object 数组")
    return value


def _session_from_row(row: sqlite3.Row) -> CasualGameSession:
    try:
        return CasualGameSession.model_validate(
            {
                "id": row["id"],
                "game_id": row["game_id"],
                "rules_version": row["rules_version"],
                "conversation_id": row["conversation_id"],
                "persona_id": row["persona_id"],
                "player_id": row["player_id"],
                "state": _json_object(row["state_json"], "session state_json"),
                "status": row["status"],
                "revision": row["revision"],
                "result_id": row["result_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "finished_at": row["finished_at"],
            }
        )
    except ValidationError as error:
        raise CasualGameDataError("数据库中的休闲游戏 Session 不符合 schema") from error


def _history_from_row(row: sqlite3.Row) -> CasualGameHistoryEntry:
    try:
        return CasualGameHistoryEntry.model_validate(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "revision": row["revision"],
                "actor_kind": row["actor_kind"],
                "actor_id": row["actor_id"],
                "idempotency_key": row["idempotency_key"],
                "expected_revision": row["expected_revision"],
                "action": _json_object(row["action_json"], "history action_json"),
                "events": _json_object_list(row["events_json"], "history events_json"),
                "response": _json_object(row["response_json"], "history response_json"),
                "created_at": row["created_at"],
            }
        )
    except ValidationError as error:
        raise CasualGameDataError("数据库中的休闲游戏历史不符合 schema") from error


def _result_from_row(row: sqlite3.Row) -> CasualGameResultRecord:
    try:
        return CasualGameResultRecord.model_validate(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "result": _json_object(row["result_json"], "result_json"),
                "chat_message_id": row["chat_message_id"],
                "memory_id": row["memory_id"],
                "memory_decision": row["memory_decision"],
                "created_at": row["created_at"],
            }
        )
    except ValidationError as error:
        raise CasualGameDataError("数据库中的休闲游戏结果不符合 schema") from error


class CasualGameRepository:
    def __init__(self, db_path: DbPath):
        self.db_path = db_path

    def init_schema(self) -> None:
        init_schema(self.db_path)

    def create_session(
        self,
        *,
        game_id: str,
        rules_version: int,
        conversation_id: str,
        persona_id: str,
        player_id: str,
        state: dict[str, Any],
        idempotency_key: str | None = None,
        creation_request: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> CasualGameSession:
        if idempotency_key is not None and creation_request is None:
            raise ValueError("creation_request is required with idempotency_key")
        session_id = session_id or new_id("casual_session")
        stamp = now_iso()
        with closing(connect(self.db_path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if idempotency_key is not None:
                    existing = connection.execute(
                        """
                        SELECT * FROM casual_game_sessions
                        WHERE conversation_id=? AND creation_key=?
                        """,
                        (conversation_id, idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        if existing["creation_request_json"] != canonical_json(creation_request):
                            raise CasualGameConflict(
                                "同一 Idempotency-Key 已用于不同的 Session 创建请求"
                            )
                        connection.commit()
                        return _session_from_row(existing)
                connection.execute(
                    """
                    INSERT INTO casual_game_sessions(
                      id,game_id,rules_version,conversation_id,persona_id,player_id,
                      creation_key,creation_request_json,state_json,status,revision,
                      result_id,created_at,updated_at,finished_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,'active',0,NULL,?,?,NULL)
                    """,
                    (
                        session_id,
                        game_id,
                        rules_version,
                        conversation_id,
                        persona_id,
                        player_id,
                        idempotency_key,
                        canonical_json(creation_request) if creation_request is not None else None,
                        canonical_json(state),
                        stamp,
                        stamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM casual_game_sessions WHERE id=?", (session_id,)
                ).fetchone()
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise CasualGameConflict("休闲游戏 Session ID 已存在") from error
            except Exception:
                connection.rollback()
                raise
        return _session_from_row(row)

    def get_session(self, session_id: str) -> CasualGameSession:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT * FROM casual_game_sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise CasualGameNotFound("休闲游戏 Session 不存在")
        return _session_from_row(row)

    def list_sessions(
        self,
        *,
        conversation_id: str | None = None,
        persona_id: str | None = None,
        status: SessionStatus | None = None,
        limit: int = 100,
    ) -> list[CasualGameSession]:
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        if persona_id is not None:
            clauses.append("persona_id=?")
            params.append(persona_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        query = "SELECT * FROM casual_game_sessions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC,id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_session_from_row(row) for row in rows]

    def find_idempotent_response(
        self,
        session_id: str,
        actor_kind: ActorKind,
        actor_id: str,
        body: ActionCommitIn,
    ) -> dict[str, Any] | None:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT actor_kind,actor_id,expected_revision,action_json,response_json
                FROM casual_game_history
                WHERE session_id=? AND idempotency_key=?
                """,
                (session_id, body.idempotency_key),
            ).fetchone()
        return self._matching_idempotent_response(row, actor_kind, actor_id, body)

    def find_persona_turn_response(
        self,
        session_id: str,
        actor_id: str,
        body: PersonaTurnIn,
    ) -> dict[str, Any] | None:
        """Return a committed Persona turn before asking the model again."""

        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT actor_kind,actor_id,expected_revision,response_json
                FROM casual_game_history
                WHERE session_id=? AND idempotency_key=?
                """,
                (session_id, body.idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if (
            row["actor_kind"] != "persona"
            or row["actor_id"] != actor_id
            or int(row["expected_revision"]) != body.expected_revision
        ):
            raise CasualGameConflict(
                "同一 idempotency_key 已用于不同的游戏动作"
            )
        return _json_object(row["response_json"], "history response_json")

    @staticmethod
    def _matching_idempotent_response(
        row: sqlite3.Row | None,
        actor_kind: ActorKind,
        actor_id: str,
        body: ActionCommitIn,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        matches = (
            row["actor_kind"] == actor_kind
            and row["actor_id"] == actor_id
            and int(row["expected_revision"]) == body.expected_revision
            and row["action_json"] == canonical_json(body.action)
        )
        if not matches:
            raise CasualGameConflict("同一 idempotency_key 已用于不同的动作")
        return _json_object(row["response_json"], "history response_json")

    def commit_action(
        self,
        session_id: str,
        actor_kind: ActorKind,
        actor_id: str,
        body: ActionCommitIn,
        *,
        next_state: dict[str, Any],
        events: list[dict[str, Any]],
        response: dict[str, Any],
        result: CasualGameResult | None = None,
    ) -> CommitOutcome:
        committed_response = dict(response)
        with closing(connect(self.db_path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT actor_kind,actor_id,expected_revision,action_json,response_json
                    FROM casual_game_history
                    WHERE session_id=? AND idempotency_key=?
                    """,
                    (session_id, body.idempotency_key),
                ).fetchone()
                stored_response = self._matching_idempotent_response(
                    existing, actor_kind, actor_id, body
                )
                if stored_response is not None:
                    connection.commit()
                    return CommitOutcome(stored_response, True)

                session = connection.execute(
                    "SELECT * FROM casual_game_sessions WHERE id=?", (session_id,)
                ).fetchone()
                if session is None:
                    raise CasualGameNotFound("休闲游戏 Session 不存在")
                if session["status"] != "active":
                    raise CasualGameConflict("休闲游戏 Session 已结束")
                current_revision = int(session["revision"])
                if body.expected_revision != current_revision:
                    raise CasualGameConflict(
                        f"revision 冲突：当前为 {current_revision}，请求为 {body.expected_revision}"
                    )

                next_revision = current_revision + 1
                stamp = now_iso()
                result_id: str | None = None
                next_status = "active"
                finished_at: str | None = None
                if result is not None:
                    if result.session_id != session_id:
                        raise CasualGameConflict("游戏结果的 session_id 与当前 Session 不一致")
                    if result.game_id != session["game_id"]:
                        raise CasualGameConflict("游戏结果的 game_id 与当前 Session 不一致")
                    result_id = new_id("casual_result")
                    committed_response["result_id"] = result_id
                    next_status = "finished"
                    finished_at = result.finished_at
                    connection.execute(
                        """
                        INSERT INTO casual_game_results(id,session_id,result_json,created_at)
                        VALUES(?,?,?,?)
                        """,
                        (result_id, session_id, canonical_json(result), stamp),
                    )

                updated = connection.execute(
                    """
                    UPDATE casual_game_sessions
                    SET state_json=?,status=?,revision=?,result_id=?,updated_at=?,finished_at=?
                    WHERE id=? AND revision=? AND status='active'
                    """,
                    (
                        canonical_json(next_state),
                        next_status,
                        next_revision,
                        result_id,
                        stamp,
                        finished_at,
                        session_id,
                        current_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise CasualGameConflict("Session 在提交动作时发生变化")

                connection.execute(
                    """
                    INSERT INTO casual_game_history(
                      id,session_id,revision,actor_kind,actor_id,idempotency_key,
                      expected_revision,action_json,events_json,response_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        new_id("casual_action"),
                        session_id,
                        next_revision,
                        actor_kind,
                        actor_id,
                        body.idempotency_key,
                        body.expected_revision,
                        canonical_json(body.action),
                        canonical_json(events),
                        canonical_json(committed_response),
                        stamp,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise CasualGameConflict("休闲游戏动作提交与现有记录冲突") from error
            except Exception:
                connection.rollback()
                raise
        return CommitOutcome(committed_response, False)

    def list_history(self, session_id: str) -> list[CasualGameHistoryEntry]:
        with closing(connect(self.db_path)) as connection:
            if connection.execute(
                "SELECT 1 FROM casual_game_sessions WHERE id=?", (session_id,)
            ).fetchone() is None:
                raise CasualGameNotFound("休闲游戏 Session 不存在")
            rows = connection.execute(
                """
                SELECT * FROM casual_game_history
                WHERE session_id=? ORDER BY revision,id
                """,
                (session_id,),
            ).fetchall()
        return [_history_from_row(row) for row in rows]

    def get_result(self, session_id: str) -> CasualGameResultRecord | None:
        with closing(connect(self.db_path)) as connection:
            session_exists = connection.execute(
                "SELECT 1 FROM casual_game_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session_exists is None:
                raise CasualGameNotFound("休闲游戏 Session 不存在")
            row = connection.execute(
                "SELECT * FROM casual_game_results WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return _result_from_row(row)

    def get_result_by_id(self, result_id: str) -> CasualGameResultRecord:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT * FROM casual_game_results WHERE id=?", (result_id,)
            ).fetchone()
        if row is None:
            raise CasualGameNotFound("休闲游戏 Result 不存在")
        return _result_from_row(row)

    def abandon_session(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> CasualGameSession:
        with closing(connect(self.db_path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM casual_game_sessions WHERE id=?", (session_id,)
                ).fetchone()
                if row is None:
                    raise CasualGameNotFound("休闲游戏 Session 不存在")
                if expected_revision is not None and int(row["revision"]) != expected_revision:
                    raise CasualGameConflict(
                        f"revision 冲突：当前为 {row['revision']}，请求为 {expected_revision}"
                    )
                if row["status"] == "finished":
                    raise CasualGameConflict("已完成的游戏不能改为放弃")
                if row["status"] == "active":
                    stamp = now_iso()
                    connection.execute(
                        """
                        UPDATE casual_game_sessions
                        SET status='abandoned',updated_at=?,finished_at=?
                        WHERE id=?
                        """,
                        (stamp, stamp, session_id),
                    )
                    row = connection.execute(
                        "SELECT * FROM casual_game_sessions WHERE id=?", (session_id,)
                    ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return _session_from_row(row)

    def get_behavior_config(self, persona_id: str, game_id: str) -> BehaviorConfig:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT * FROM casual_game_behavior_configs
                WHERE persona_id=? AND game_id=?
                """,
                (persona_id, game_id),
            ).fetchone()
        if row is None:
            return BehaviorConfig(persona_id=persona_id, game_id=game_id)
        return BehaviorConfig.model_validate(dict(row))

    def list_behavior_configs(self, persona_id: str) -> list[BehaviorConfig]:
        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT * FROM casual_game_behavior_configs
                WHERE persona_id=? ORDER BY game_id
                """,
                (persona_id,),
            ).fetchall()
        return [BehaviorConfig.model_validate(dict(row)) for row in rows]

    def upsert_behavior_config(
        self,
        persona_id: str,
        game_id: str,
        body: BehaviorConfigUpdate,
    ) -> BehaviorConfig:
        stamp = now_iso()
        with closing(connect(self.db_path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO casual_game_behavior_configs(
                      persona_id,game_id,instructions,strategy_instructions,
                      reaction_instructions,memory_instructions,memory_mode,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(persona_id,game_id) DO UPDATE SET
                      instructions=excluded.instructions,
                      strategy_instructions=excluded.strategy_instructions,
                      reaction_instructions=excluded.reaction_instructions,
                      memory_instructions=excluded.memory_instructions,
                      memory_mode=excluded.memory_mode,
                      updated_at=excluded.updated_at
                    """,
                    (
                        persona_id,
                        game_id,
                        body.instructions,
                        body.strategy_instructions,
                        body.reaction_instructions,
                        body.memory_instructions,
                        body.memory_mode,
                        stamp,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM casual_game_behavior_configs
                    WHERE persona_id=? AND game_id=?
                    """,
                    (persona_id, game_id),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return BehaviorConfig.model_validate(dict(row))

    def delete_behavior_config(self, persona_id: str, game_id: str) -> bool:
        with closing(connect(self.db_path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    DELETE FROM casual_game_behavior_configs
                    WHERE persona_id=? AND game_id=?
                    """,
                    (persona_id, game_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return cursor.rowcount == 1
