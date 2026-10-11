from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.game.engine import (
    ai_character_action_events,
    canonical_json,
    player_action_events,
    state_digest,
    validate_and_apply_events,
)
from backend.game.events import WorldEvent, parse_world_event
from backend.game.models import (
    AICharacterActionIn,
    AICharacterPersonaSnapshot,
    AICharacterProviderSnapshot,
    AICharacterResolvedRuntime,
    AICharacterSeedIn,
    AICharacterState,
    BranchCreateIn,
    FactState,
    LocationPosition,
    LocationState,
    NPCState,
    PlayerActionIn,
    PlayerRef,
    PlayerState,
    QuestState,
    SaveCreateIn,
    SessionCreateIn,
    TurnNarration,
    TurnActionIn,
    WorldClock,
    WorldCreateIn,
    WorldState,
    ItemState,
)
from backend.game.rules import (
    RuleViolation,
    validate_ai_character_event_authority,
    validate_player_event_authority,
    validate_world_state,
)
from backend.game.schema import DbPath, connect, init_schema


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def json_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def ai_character_runtime_hash(runtime: AICharacterResolvedRuntime) -> str:
    return json_hash(runtime)


class GameNotFound(LookupError):
    pass


class GameConflict(RuntimeError):
    pass


class GameDataError(RuntimeError):
    pass


def validated_stored_state(
    raw_state: str,
    expected_hash: str,
    *,
    session_id: str,
    revision: int,
    label: str,
) -> WorldState:
    try:
        state = WorldState.model_validate_json(raw_state)
        validate_world_state(state)
    except Exception as error:
        raise GameDataError(f"{label} 不是有效的 WorldState：{error}") from error
    canonical = canonical_json(state)
    if raw_state != canonical:
        raise GameDataError(f"{label} 未使用规范 JSON 序列化")
    if state_digest(state) != expected_hash:
        raise GameDataError(f"{label} 内容与权威哈希不一致")
    if state.session_id != session_id or state.revision != revision:
        raise GameDataError(f"{label} 的 Session 或 revision 元数据不一致")
    return state


def validated_stored_event(
    row: sqlite3.Row,
    *,
    session_id: str,
    revision: int,
    sequence: int,
) -> WorldEvent:
    try:
        event = parse_world_event(json.loads(row["event_json"]))
    except Exception as error:
        raise GameDataError(f"revision {revision} sequence {sequence} 的领域事件无效：{error}") from error
    if canonical_json(event) != row["event_json"]:
        raise GameDataError(f"revision {revision} sequence {sequence} 的事件 JSON 不是规范形式")
    if row["session_id"] != session_id or row["turn_session_id"] != session_id:
        raise GameDataError(f"revision {revision} sequence {sequence} 的 Session 元数据不一致")
    if int(row["revision"]) != revision or int(row["turn_revision"]) != revision:
        raise GameDataError(f"revision {revision} sequence {sequence} 的 turn revision 不一致")
    if int(row["turn_expected_revision"]) != revision - 1:
        raise GameDataError(f"revision {revision} 的 turn expected_revision 不一致")
    if int(row["sequence"]) != sequence:
        raise GameDataError(f"revision {revision} 的事件 sequence 不连续")
    if row["id"] != event.event_id or row["event_type"] != event.type:
        raise GameDataError(f"revision {revision} sequence {sequence} 的事件列与 JSON 不一致")
    return event


def validated_stored_turn(turn: sqlite3.Row) -> TurnActionIn:
    try:
        intent = json.loads(turn["intent_json"])
        action_type: type[PlayerActionIn] | type[AICharacterActionIn]
        if turn["actor_kind"] == "player":
            action_type = PlayerActionIn
        elif turn["actor_kind"] == "ai_character":
            action_type = AICharacterActionIn
        else:
            raise ValueError(f"不支持的 turn actor_kind：{turn['actor_kind']}")
        action = action_type.model_validate({
            "actor": {"kind": turn["actor_kind"], "id": turn["actor_id"]},
            "intent": intent,
            "content": turn["content"],
            "expected_revision": int(turn["expected_revision"]),
            "idempotency_key": turn["idempotency_key"],
        })
    except Exception as error:
        raise GameDataError(f"turn {turn['id']} 的行动元数据无效：{error}") from error
    if canonical_json(action.intent) != turn["intent_json"]:
        raise GameDataError(f"turn {turn['id']} 的 intent JSON 不是规范形式")
    if int(turn["revision"]) != action.expected_revision + 1:
        raise GameDataError(f"turn {turn['id']} 的 revision 链不连续")
    if json_hash(action) != turn["request_hash"]:
        raise GameDataError(f"turn {turn['id']} 的请求内容与哈希不一致")
    return action


def validated_stored_turn_output(
    row: sqlite3.Row,
    *,
    turn_id: str,
    session_id: str,
) -> TurnNarration:
    try:
        narration = TurnNarration.model_validate_json(row["output_json"])
    except Exception as error:
        raise GameDataError(f"turn {turn_id} 的叙事输出不符合 schema：{error}") from error
    if row["turn_id"] != turn_id or row["session_id"] != session_id:
        raise GameDataError(f"turn {turn_id} 的叙事输出元数据不一致")
    if canonical_json(narration) != row["output_json"]:
        raise GameDataError(f"turn {turn_id} 的叙事输出 JSON 不是规范形式")
    if json_hash(narration) != row["output_hash"]:
        raise GameDataError(f"turn {turn_id} 的叙事输出与权威哈希不一致")
    return narration


def validated_stored_ai_character_runtime(row: sqlite3.Row) -> AICharacterResolvedRuntime:
    try:
        persona = AICharacterPersonaSnapshot.model_validate_json(row["persona_snapshot_json"])
        provider = AICharacterProviderSnapshot.model_validate_json(row["config_json"])
        runtime = AICharacterResolvedRuntime(
            persona_id=row["persona_id"],
            provider_id=row["provider_id"],
            persona=persona,
            provider=provider,
        )
    except Exception as error:
        raise GameDataError(
            f"AICharacter {row['actor_id']} 的运行快照不符合安全 schema"
        ) from error
    if canonical_json(persona) != row["persona_snapshot_json"]:
        raise GameDataError(f"AICharacter {row['actor_id']} 的 Persona 快照不是规范 JSON")
    if canonical_json(provider) != row["config_json"]:
        raise GameDataError(f"AICharacter {row['actor_id']} 的 Provider 快照不是规范 JSON")
    return runtime


@dataclass(frozen=True)
class ActionPreparation:
    committed: dict[str, Any] | None = None
    state: WorldState | None = None
    state_hash: str | None = None


class GameRepository:
    def __init__(
        self,
        db_path: DbPath,
        ai_character_resolver: Callable[[str, str], object | None] | None = None,
    ):
        self.db_path = db_path
        self.ai_character_resolver = ai_character_resolver

    def init(self) -> None:
        init_schema(self.db_path)

    @staticmethod
    def _operation_result_id(
        connection: sqlite3.Connection,
        operation_type: str,
        operation_scope: str,
        idempotency_key: str | None,
        request_hash: str,
    ) -> str | None:
        if idempotency_key is None:
            return None
        row = connection.execute(
            """
            SELECT request_hash,result_id
            FROM world_game_operations
            WHERE operation_type=? AND operation_scope=? AND idempotency_key=?
            """,
            (operation_type, operation_scope, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise GameConflict("同一个幂等键不能用于不同的持久化请求")
        return str(row["result_id"])

    @staticmethod
    def _record_operation(
        connection: sqlite3.Connection,
        operation_type: str,
        operation_scope: str,
        idempotency_key: str | None,
        request_hash: str,
        result_id: str,
        created_at: str,
    ) -> None:
        if idempotency_key is None:
            return
        connection.execute(
            """
            INSERT INTO world_game_operations(
              operation_type,operation_scope,idempotency_key,request_hash,result_id,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                operation_type,
                operation_scope,
                idempotency_key,
                request_hash,
                result_id,
                created_at,
            ),
        )

    def _resolve_ai_character_runtime(
        self,
        seed: AICharacterSeedIn,
    ) -> AICharacterResolvedRuntime:
        if self.ai_character_resolver is None:
            raise RuleViolation(
                "ai_character_runtime_unavailable",
                "当前运行环境不能解析 AICharacter 的 Persona/Provider",
            )
        raw_runtime = self.ai_character_resolver(seed.persona_id, seed.provider_id)
        if raw_runtime is None:
            raise RuleViolation(
                "ai_character_runtime_not_found",
                f"AICharacter {seed.id} 的 Persona 或已启用 Provider 不存在",
            )
        try:
            runtime = AICharacterResolvedRuntime.model_validate(raw_runtime)
        except Exception as error:
            raise RuleViolation(
                "ai_character_runtime_invalid",
                f"AICharacter {seed.id} 的运行配置不符合安全快照 schema",
            ) from error
        if runtime.persona_id != seed.persona_id or runtime.provider_id != seed.provider_id:
            raise RuleViolation(
                "ai_character_runtime_mismatch",
                f"AICharacter {seed.id} 的运行快照与所选 Persona/Provider 不一致",
            )
        return runtime

    def _load_ai_character_runtime(
        self,
        connection: sqlite3.Connection,
        state: WorldState,
        actor_id: str,
    ) -> AICharacterResolvedRuntime:
        resident = state.ai_characters.get(actor_id)
        if resident is None:
            raise RuleViolation("ai_character_not_found", "AICharacter 不属于当前 Session")
        row = connection.execute(
            """
            SELECT actor_id,persona_id,provider_id,persona_snapshot_json,config_json
            FROM world_game_ai_characters WHERE session_id=? AND actor_id=?
            """,
            (state.session_id, actor_id),
        ).fetchone()
        if row is None:
            raise GameDataError(f"AICharacter {actor_id} 缺少运行快照")
        runtime = validated_stored_ai_character_runtime(row)
        if resident.persona_id != runtime.persona_id or resident.provider_id != runtime.provider_id:
            raise GameDataError(f"AICharacter {actor_id} 的 WorldState 与运行快照身份不一致")
        if resident.runtime_hash != ai_character_runtime_hash(runtime):
            raise GameDataError(f"AICharacter {actor_id} 的运行快照与 WorldState 哈希不一致")
        return runtime

    def _validate_ai_character_runtime_set(
        self,
        connection: sqlite3.Connection,
        state: WorldState,
    ) -> None:
        rows = list(connection.execute(
            """
            SELECT actor_id,persona_id,provider_id,persona_snapshot_json,config_json
            FROM world_game_ai_characters WHERE session_id=?
            """,
            (state.session_id,),
        ))
        if {row["actor_id"] for row in rows} != set(state.ai_characters):
            raise GameDataError("WorldState 与 AICharacter 运行快照集合不一致")
        for row in rows:
            resident = state.ai_characters[row["actor_id"]]
            runtime = validated_stored_ai_character_runtime(row)
            if (
                resident.persona_id != runtime.persona_id
                or resident.provider_id != runtime.provider_id
                or resident.runtime_hash != ai_character_runtime_hash(runtime)
            ):
                raise GameDataError(
                    f"AICharacter {resident.id} 的 WorldState 与运行快照不一致"
                )

    def get_ai_character_runtime(
        self,
        session_id: str,
        actor_id: str,
    ) -> AICharacterResolvedRuntime:
        with closing(connect(self.db_path)) as connection:
            _session, state = self._load_current_session_state(connection, session_id)
            return self._load_ai_character_runtime(connection, state, actor_id)

    def create_world(
        self,
        body: WorldCreateIn,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        request_hash = json_hash(body)
        with closing(connect(self.db_path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing_id = self._operation_result_id(
                    connection,
                    "create_world",
                    "game-worlds",
                    idempotency_key,
                    request_hash,
                )
                if existing_id is not None:
                    connection.commit()
                    return self.get_world(existing_id)

                stamp = now_iso()
                world_id = new_id("world")
                version_id = new_id("worldv")
                definition_json = canonical_json(body)
                definition_hash = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT INTO world_game_worlds VALUES(?,?,?,?,?,?)",
                    (world_id, body.name, body.description, 1, stamp, stamp),
                )
                connection.execute(
                    "INSERT INTO world_game_versions VALUES(?,?,?,?,?,?)",
                    (version_id, world_id, 1, definition_json, definition_hash, stamp),
                )
                self._record_operation(
                    connection,
                    "create_world",
                    "game-worlds",
                    idempotency_key,
                    request_hash,
                    world_id,
                    stamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "id": world_id,
            "name": body.name,
            "description": body.description,
            "current_version": 1,
            "version": {
                "id": version_id,
                "version": 1,
                "definition": body.model_dump(mode="json"),
                "definition_hash": definition_hash,
            },
            "created_at": stamp,
            "updated_at": stamp,
        }

    def get_world(self, world_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT w.*,v.id AS version_id,v.definition_json,v.definition_hash,v.created_at AS version_created_at
                FROM world_game_worlds w
                JOIN world_game_versions v ON v.world_id=w.id AND v.version=w.current_version
                WHERE w.id=?
                """,
                (world_id,),
            ).fetchone()
        if not row:
            raise GameNotFound("世界不存在")
        definition = WorldCreateIn.model_validate_json(row["definition_json"])
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "current_version": row["current_version"],
            "version": {
                "id": row["version_id"],
                "version": row["current_version"],
                "definition": definition.model_dump(mode="json"),
                "definition_hash": row["definition_hash"],
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_worlds(self) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT w.id,w.name,w.description,w.current_version,w.created_at,w.updated_at,
                       COUNT(s.id) AS session_count
                FROM world_game_worlds w
                LEFT JOIN world_game_sessions s ON s.world_id=w.id
                GROUP BY w.id,w.name,w.description,w.current_version,w.created_at,w.updated_at
                ORDER BY w.updated_at DESC,w.id DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "current_version": int(row["current_version"]),
                "session_count": int(row["session_count"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def _initial_state(
        self,
        definition: WorldCreateIn,
        world_id: str,
        version_id: str,
        session_id: str,
        player_id: str,
        player_display_name: str,
        ai_characters: list[AICharacterSeedIn],
        ai_character_runtimes: dict[str, AICharacterResolvedRuntime],
    ) -> WorldState:
        player_ref = PlayerRef(id=player_id)
        state = WorldState(
            world_id=world_id,
            world_version_id=version_id,
            session_id=session_id,
            revision=0,
            turn=0,
            clock=WorldClock(total_minutes=0),
            player=PlayerState(
                id=player_id,
                display_name=player_display_name,
                location_id=definition.starting_location_id,
            ),
            ai_characters={
                actor.id: AICharacterState(
                    id=actor.id,
                    display_name=actor.display_name,
                    persona_id=actor.persona_id,
                    provider_id=actor.provider_id,
                    runtime_hash=ai_character_runtime_hash(ai_character_runtimes[actor.id]),
                    location_id=actor.initial_location_id or definition.starting_location_id,
                    active=actor.active,
                )
                for actor in ai_characters
            },
            locations={
                location.id: LocationState(
                    **location.model_dump(mode="json"),
                    visited=location.id == definition.starting_location_id,
                )
                for location in definition.locations
            },
            items={
                item.id: ItemState(
                    id=item.id,
                    name=item.name,
                    description=item.description,
                    quantity=item.quantity,
                    position=LocationPosition(location_id=item.initial_location_id),
                )
                for item in definition.items
            },
            npcs={
                npc.id: NPCState(
                    id=npc.id,
                    name=npc.name,
                    description=npc.description,
                    location_id=npc.initial_location_id,
                )
                for npc in definition.npcs
            },
            facts={
                fact.id: FactState(
                    id=fact.id,
                    text=fact.text,
                    known_by=[player_ref] if fact.initially_known_by_player else [],
                )
                for fact in definition.facts
            },
            quests={
                quest.id: QuestState(
                    id=quest.id,
                    title=quest.title,
                    description=quest.description,
                    status=quest.initial_status,
                )
                for quest in definition.quests
            },
        )
        validate_world_state(state)
        return state

    def create_session(
        self,
        body: SessionCreateIn,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        request_hash = json_hash(body)
        with closing(connect(self.db_path)) as connection:
            try:
                transaction_started = False
                if idempotency_key is not None:
                    connection.execute("BEGIN IMMEDIATE")
                    transaction_started = True
                    existing_id = self._operation_result_id(
                        connection,
                        "create_session",
                        "game-sessions",
                        idempotency_key,
                        request_hash,
                    )
                    if existing_id is not None:
                        connection.commit()
                        return self.get_session(existing_id)

                world = connection.execute(
                    """
                    SELECT w.id,w.current_version,v.id AS version_id,v.definition_json
                    FROM world_game_worlds w
                    JOIN world_game_versions v ON v.world_id=w.id AND v.version=w.current_version
                    WHERE w.id=?
                    """,
                    (body.world_id,),
                ).fetchone()
                if not world:
                    raise GameNotFound("世界不存在")
                definition = WorldCreateIn.model_validate_json(world["definition_json"])
                runtimes = {
                    actor.id: self._resolve_ai_character_runtime(actor)
                    for actor in body.ai_characters
                }
                session_id = new_id("session")
                player_id = body.player_id or new_id("player")
                stamp = now_iso()
                state = self._initial_state(
                    definition,
                    world["id"],
                    world["version_id"],
                    session_id,
                    player_id,
                    body.player_display_name,
                    body.ai_characters,
                    runtimes,
                )
                state_json = canonical_json(state)
                digest = state_digest(state)
                if not transaction_started:
                    connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO world_game_sessions(
                      id,world_id,world_version_id,branch_name,parent_session_id,parent_revision,
                      player_id,current_revision,initial_state_json,current_state_json,state_hash,created_at,updated_at
                    ) VALUES(?,?,?,?,NULL,NULL,?,0,?,?,?,?,?)
                    """,
                    (
                        session_id, world["id"], world["version_id"], body.branch_name,
                        player_id, state_json, state_json, digest, stamp, stamp,
                    ),
                )
                connection.execute(
                    "INSERT INTO world_game_players VALUES(?,?,?,?)",
                    (session_id, player_id, body.player_display_name, stamp),
                )
                for npc in definition.npcs:
                    connection.execute(
                        "INSERT INTO world_game_npcs VALUES(?,?,?,?)",
                        (session_id, npc.id, npc.id, stamp),
                    )
                for actor in body.ai_characters:
                    runtime = runtimes[actor.id]
                    connection.execute(
                        """
                        INSERT INTO world_game_ai_characters(
                          session_id,actor_id,persona_id,provider_id,
                          persona_snapshot_json,config_json,created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            session_id,
                            actor.id,
                            runtime.persona_id,
                            runtime.provider_id,
                            canonical_json(runtime.persona),
                            canonical_json(runtime.provider),
                            stamp,
                        ),
                    )
                connection.execute(
                    "INSERT INTO world_game_snapshots VALUES(?,?,?,?,?)",
                    (session_id, 0, state_json, digest, stamp),
                )
                self._record_operation(
                    connection,
                    "create_session",
                    "game-sessions",
                    idempotency_key,
                    request_hash,
                    session_id,
                    stamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_session(session_id)

    def _load_current_session_state(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> tuple[sqlite3.Row, WorldState]:
        row = connection.execute(
            "SELECT * FROM world_game_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not row:
            raise GameNotFound("Session 不存在")
        state = validated_stored_state(
            row["current_state_json"],
            row["state_hash"],
            session_id=row["id"],
            revision=int(row["current_revision"]),
            label="当前 WorldState",
        )
        self._validate_ai_character_runtime_set(connection, state)
        return row, state

    def get_session(self, session_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            row, state = self._load_current_session_state(connection, session_id)
        return {
            "id": row["id"],
            "world_id": row["world_id"],
            "world_version_id": row["world_version_id"],
            "branch_name": row["branch_name"],
            "parent_session_id": row["parent_session_id"],
            "parent_revision": row["parent_revision"],
            "current_revision": row["current_revision"],
            "state_hash": row["state_hash"],
            "state": state.model_dump(mode="json"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_sessions(self, world_id: str | None = None) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT s.id,w.name AS world_name
                FROM world_game_sessions s
                JOIN world_game_worlds w ON w.id=s.world_id
                WHERE (? IS NULL OR s.world_id=?)
                ORDER BY s.updated_at DESC,s.id DESC
                """,
                (world_id, world_id),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for summary in rows:
                row, state = self._load_current_session_state(connection, summary["id"])
                result.append({
                    "id": row["id"],
                    "world_id": row["world_id"],
                    "world_name": summary["world_name"],
                    "branch_name": row["branch_name"],
                    "parent_session_id": row["parent_session_id"],
                    "parent_revision": row["parent_revision"],
                    "current_revision": int(row["current_revision"]),
                    "state_hash": row["state_hash"],
                    "player": state.player.model_dump(mode="json"),
                    "ai_character_count": len(state.ai_characters),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
        return result

    def _committed_action_payload(
        self,
        connection: sqlite3.Connection,
        turn: sqlite3.Row,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        validated_stored_turn(turn)
        event_rows = list(connection.execute(
            """
            SELECT
              e.id,e.session_id,e.turn_id,e.revision,e.sequence,e.event_type,e.event_json,
              t.session_id AS turn_session_id,t.revision AS turn_revision,
              t.expected_revision AS turn_expected_revision
            FROM world_game_events e
            JOIN world_game_turns t ON t.id=e.turn_id
            WHERE e.turn_id=?
            ORDER BY e.sequence,e.id
            """,
            (turn["id"],),
        ))
        if not event_rows:
            raise GameDataError("已提交回合没有领域事件")
        events = [
            validated_stored_event(
                row,
                session_id=turn["session_id"],
                revision=int(turn["revision"]),
                sequence=sequence,
            ).model_dump(mode="json")
            for sequence, row in enumerate(event_rows)
        ]
        snapshot = connection.execute(
            "SELECT state_json,state_hash FROM world_game_snapshots WHERE session_id=? AND revision=?",
            (turn["session_id"], turn["revision"]),
        ).fetchone()
        if not snapshot:
            raise GameDataError("已提交回合缺少对应快照")
        state = validated_stored_state(
            snapshot["state_json"],
            snapshot["state_hash"],
            session_id=turn["session_id"],
            revision=int(turn["revision"]),
            label="已提交回合快照",
        )
        output_row = connection.execute(
            """
            SELECT turn_id,session_id,output_json,output_hash,created_at
            FROM world_game_turn_outputs WHERE turn_id=?
            """,
            (turn["id"],),
        ).fetchone()
        narration = (
            validated_stored_turn_output(
                output_row,
                turn_id=turn["id"],
                session_id=turn["session_id"],
            )
            if output_row is not None
            else None
        )
        payload = {
            "action_id": turn["id"],
            "session_id": turn["session_id"],
            "committed_revision": turn["revision"],
            "idempotent_replay": idempotent_replay,
            "events": events,
            "state_hash": snapshot["state_hash"],
            "state": state.model_dump(mode="json"),
        }
        # Keep the deterministic /actions response contract byte-for-field
        # compatible: only GM/Narrator turns have this key.
        if narration is not None:
            payload["narration"] = narration.model_dump(mode="json")
        return payload

    def _validate_action_actor(
        self,
        connection: sqlite3.Connection,
        state: WorldState,
        body: TurnActionIn,
    ) -> None:
        if isinstance(body, PlayerActionIn):
            if body.actor.id != state.player.id:
                raise RuleViolation(
                    "player_mismatch",
                    "Player 行动者与当前 Session 的真人玩家不一致",
                )
            return
        resident = state.ai_characters.get(body.actor.id)
        if resident is None:
            raise RuleViolation("ai_character_not_found", "AICharacter 不属于当前 Session")
        if not resident.active:
            raise RuleViolation("ai_character_inactive", "AICharacter 当前未启用")
        self._load_ai_character_runtime(connection, state, body.actor.id)

    @staticmethod
    def _validate_action_event_authority(
        state: WorldState,
        body: TurnActionIn,
        events: list[WorldEvent],
    ) -> None:
        if isinstance(body, PlayerActionIn):
            validate_player_event_authority(state, body, events)
        else:
            validate_ai_character_event_authority(state, body, events)

    def _prepare_action(
        self,
        session_id: str,
        body: TurnActionIn,
    ) -> ActionPreparation:
        request_hash = json_hash(body)
        with closing(connect(self.db_path)) as connection:
            session, current_state = self._load_current_session_state(connection, session_id)
            self._validate_action_actor(connection, current_state, body)
            existing = connection.execute(
                "SELECT * FROM world_game_turns WHERE session_id=? AND idempotency_key=?",
                (session_id, body.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise GameConflict("同一个幂等键不能用于不同的行动")
                return ActionPreparation(
                    committed=self._committed_action_payload(
                        connection,
                        existing,
                        idempotent_replay=True,
                    )
                )

            current_revision = int(session["current_revision"])
            if body.expected_revision != current_revision:
                raise GameConflict(
                    f"WorldState revision 已变化：期望 {body.expected_revision}，当前 {current_revision}"
                )
            digest = state_digest(current_state)
            if digest != session["state_hash"]:
                raise GameDataError("当前 WorldState 内容与权威哈希不一致")
            return ActionPreparation(state=current_state, state_hash=digest)

    def prepare_player_action(
        self,
        session_id: str,
        body: PlayerActionIn,
    ) -> ActionPreparation:
        """Resolve cheap conflicts before invoking a potentially expensive GM."""

        return self._prepare_action(session_id, body)

    def prepare_ai_character_action(
        self,
        session_id: str,
        body: AICharacterActionIn,
    ) -> ActionPreparation:
        """Preflight an internal AI resident turn without exposing an HTTP actor switch."""

        return self._prepare_action(session_id, body)

    def _commit_action_events(
        self,
        session_id: str,
        body: TurnActionIn,
        event_builder: Callable[[WorldState, str], list[WorldEvent]],
        *,
        based_on_state_hash: str | None = None,
        narration: TurnNarration | None = None,
    ) -> dict[str, Any]:
        request_hash = json_hash(body)
        stamp = now_iso()
        with closing(connect(self.db_path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                session, current_state = self._load_current_session_state(connection, session_id)
                self._validate_action_actor(connection, current_state, body)
                existing = connection.execute(
                    "SELECT * FROM world_game_turns WHERE session_id=? AND idempotency_key=?",
                    (session_id, body.idempotency_key),
                ).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise GameConflict("同一个幂等键不能用于不同的行动")
                    payload = self._committed_action_payload(
                        connection, existing, idempotent_replay=True
                    )
                    connection.commit()
                    return payload

                current_revision = int(session["current_revision"])
                if body.expected_revision != current_revision:
                    raise GameConflict(
                        f"WorldState revision 已变化：期望 {body.expected_revision}，当前 {current_revision}"
                    )
                current_hash = state_digest(current_state)
                if current_hash != session["state_hash"]:
                    raise GameDataError("当前 WorldState 内容与权威哈希不一致")
                if based_on_state_hash is not None and based_on_state_hash != current_hash:
                    raise GameConflict("候选变化基于过期的 WorldState hash")
                events = event_builder(current_state, current_hash)
                self._validate_action_event_authority(current_state, body, events)
                next_revision = current_revision + 1
                next_state = validate_and_apply_events(current_state, events, next_revision)
                next_json = canonical_json(next_state)
                next_hash = state_digest(next_state)
                turn_id = new_id("turn")
                connection.execute(
                    """
                    INSERT INTO world_game_turns(
                      id,session_id,revision,idempotency_key,request_hash,actor_kind,actor_id,
                      intent_json,content,expected_revision,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        turn_id, session_id, next_revision, body.idempotency_key, request_hash,
                        body.actor.kind, body.actor.id, canonical_json(body.intent), body.content,
                        body.expected_revision, stamp,
                    ),
                )
                for sequence, event in enumerate(events):
                    connection.execute(
                        "INSERT INTO world_game_events VALUES(?,?,?,?,?,?,?,?)",
                        (
                            event.event_id, session_id, turn_id, next_revision, sequence,
                            event.type, canonical_json(event), stamp,
                        ),
                    )
                if narration is not None:
                    output_json = canonical_json(narration)
                    connection.execute(
                        """
                        INSERT INTO world_game_turn_outputs(
                          turn_id,session_id,output_json,output_hash,created_at
                        ) VALUES(?,?,?,?,?)
                        """,
                        (
                            turn_id,
                            session_id,
                            output_json,
                            json_hash(narration),
                            stamp,
                        ),
                    )
                updated = connection.execute(
                    """
                    UPDATE world_game_sessions
                    SET current_revision=?,current_state_json=?,state_hash=?,updated_at=?
                    WHERE id=? AND current_revision=?
                    """,
                    (next_revision, next_json, next_hash, stamp, session_id, current_revision),
                )
                if updated.rowcount != 1:
                    raise GameConflict("WorldState revision 已被其他行动更新")
                connection.execute(
                    "INSERT INTO world_game_snapshots VALUES(?,?,?,?,?)",
                    (session_id, next_revision, next_json, next_hash, stamp),
                )
                turn = connection.execute(
                    "SELECT * FROM world_game_turns WHERE id=?", (turn_id,)
                ).fetchone()
                payload = self._committed_action_payload(
                    connection, turn, idempotent_replay=False
                )
                connection.commit()
                return payload
            except Exception:
                connection.rollback()
                raise

    def commit_player_action(self, session_id: str, body: PlayerActionIn) -> dict[str, Any]:
        return self._commit_action_events(
            session_id,
            body,
            lambda state, _state_hash: player_action_events(state, body),
        )

    def commit_player_events(
        self,
        session_id: str,
        body: PlayerActionIn,
        based_on_state_hash: str,
        events: list[WorldEvent],
        narration: TurnNarration | None = None,
    ) -> dict[str, Any]:
        trusted_narration = (
            TurnNarration.model_validate_json(narration.model_dump_json())
            if narration is not None
            else None
        )

        def supplied_events(_state: WorldState, _state_hash: str) -> list[WorldEvent]:
            return [
                parse_world_event(event.model_dump(mode="json"))
                for event in events
            ]

        return self._commit_action_events(
            session_id,
            body,
            supplied_events,
            based_on_state_hash=based_on_state_hash,
            narration=trusted_narration,
        )

    def commit_ai_character_action(
        self,
        session_id: str,
        body: AICharacterActionIn,
    ) -> dict[str, Any]:
        return self._commit_action_events(
            session_id,
            body,
            lambda state, _state_hash: ai_character_action_events(state, body),
        )

    def commit_ai_character_events(
        self,
        session_id: str,
        body: AICharacterActionIn,
        based_on_state_hash: str,
        events: list[WorldEvent],
    ) -> dict[str, Any]:
        def supplied_events(_state: WorldState, _state_hash: str) -> list[WorldEvent]:
            return [parse_world_event(event.model_dump(mode="json")) for event in events]

        return self._commit_action_events(
            session_id,
            body,
            supplied_events,
            based_on_state_hash=based_on_state_hash,
        )

    def replay(self, session_id: str, revision: int | None = None) -> WorldState:
        with closing(connect(self.db_path)) as connection:
            session = connection.execute(
                "SELECT * FROM world_game_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not session:
                raise GameNotFound("Session 不存在")
            current_revision = int(session["current_revision"])
            target_revision = current_revision if revision is None else revision
            if target_revision < 0 or target_revision > current_revision:
                raise GameConflict("目标 revision 不在当前 Session 的事件范围内")
            rows = list(connection.execute(
                """
                SELECT
                  e.id,e.session_id,e.turn_id,e.revision,e.sequence,e.event_type,e.event_json,
                  t.session_id AS turn_session_id,t.revision AS turn_revision,
                  t.expected_revision AS turn_expected_revision
                FROM world_game_events e
                JOIN world_game_turns t ON t.id=e.turn_id
                WHERE e.session_id=? AND e.revision<=?
                ORDER BY e.revision,e.sequence,e.id
                """,
                (session_id, target_revision),
            ))
            turn_rows = list(connection.execute(
                """
                SELECT * FROM world_game_turns
                WHERE session_id=? AND revision<=?
                ORDER BY revision,id
                """,
                (session_id, target_revision),
            ))
            expected_snapshot = connection.execute(
                "SELECT state_json,state_hash FROM world_game_snapshots WHERE session_id=? AND revision=?",
                (session_id, target_revision),
            ).fetchone()
            if not expected_snapshot:
                raise GameDataError(f"缺少 revision {target_revision} 的权威快照")
            initial_snapshot = connection.execute(
                "SELECT state_json,state_hash FROM world_game_snapshots WHERE session_id=? AND revision=0",
                (session_id,),
            ).fetchone()
            if not initial_snapshot:
                raise GameDataError("缺少 revision 0 的初始快照")
            if session["initial_state_json"] != initial_snapshot["state_json"]:
                raise GameDataError("Session 初始状态与 revision 0 快照不一致")
            state = validated_stored_state(
                initial_snapshot["state_json"],
                initial_snapshot["state_hash"],
                session_id=session_id,
                revision=0,
                label="初始 WorldState",
            )
            target_snapshot_state = validated_stored_state(
                expected_snapshot["state_json"],
                expected_snapshot["state_hash"],
                session_id=session_id,
                revision=target_revision,
                label=f"revision {target_revision} 快照",
            )

        turns_by_revision: dict[int, tuple[sqlite3.Row, TurnActionIn]] = {}
        for expected_revision, turn in enumerate(turn_rows, start=1):
            if int(turn["revision"]) != expected_revision:
                raise GameDataError(f"turn 日志缺少或重复 revision {expected_revision}")
            turns_by_revision[expected_revision] = (turn, validated_stored_turn(turn))
        if len(turns_by_revision) != target_revision:
            raise GameDataError("turn 日志与目标 revision 数量不一致")

        grouped_rows: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            row_revision = int(row["revision"])
            if row_revision < 1:
                raise GameDataError("事件日志包含 revision 1 之前的事件")
            grouped_rows[row_revision].append(row)
        for next_revision in range(1, target_revision + 1):
            revision_rows = grouped_rows.get(next_revision)
            if not revision_rows:
                raise GameDataError(f"事件日志缺少 revision {next_revision}")
            turn_ids = {row["turn_id"] for row in revision_rows}
            turn, action = turns_by_revision[next_revision]
            if turn_ids != {turn["id"]}:
                raise GameDataError(f"revision {next_revision} 的事件没有绑定到对应 turn")
            events = [
                validated_stored_event(
                    row,
                    session_id=session_id,
                    revision=next_revision,
                    sequence=sequence,
                )
                for sequence, row in enumerate(revision_rows)
            ]
            try:
                self._validate_action_event_authority(state, action, events)
                state = validate_and_apply_events(state, events, next_revision)
            except RuleViolation as error:
                raise GameDataError(
                    f"revision {next_revision} 的已提交事件不再满足权限或领域规则：{error.message}"
                ) from error
        if canonical_json(state) != canonical_json(target_snapshot_state):
            raise GameDataError(f"revision {target_revision} 的事件重放结果与权威快照不一致")
        return state

    def replay_result(self, session_id: str, revision: int | None = None) -> dict[str, Any]:
        replayed = self.replay(session_id, revision)
        replay_hash = state_digest(replayed)
        session = self.get_session(session_id)
        is_current = revision is None or revision == session["current_revision"]
        return {
            "session_id": session_id,
            "revision": replayed.revision,
            "state": replayed.model_dump(mode="json"),
            "state_hash": replay_hash,
            "matches_current": is_current and replay_hash == session["state_hash"],
        }

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            session = connection.execute(
                "SELECT current_revision FROM world_game_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if not session:
                raise GameNotFound("Session 不存在")
            rows = list(connection.execute(
                """
                SELECT
                  e.id,e.session_id,e.turn_id,e.revision,e.sequence,e.event_type,e.event_json,e.created_at,
                  t.session_id AS turn_session_id,t.revision AS turn_revision,
                  t.expected_revision AS turn_expected_revision
                FROM world_game_events e
                JOIN world_game_turns t ON t.id=e.turn_id
                WHERE e.session_id=? ORDER BY e.revision,e.sequence,e.id
                """,
                (session_id,),
            ))
        grouped_rows: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped_rows[int(row["revision"])].append(row)
        result: list[dict[str, Any]] = []
        for revision_number in range(1, int(session["current_revision"]) + 1):
            revision_rows = grouped_rows.get(revision_number)
            if not revision_rows:
                raise GameDataError(f"事件日志缺少 revision {revision_number}")
            if len({row["turn_id"] for row in revision_rows}) != 1:
                raise GameDataError(f"revision {revision_number} 对应了多个 turn")
            for sequence, row in enumerate(revision_rows):
                event = validated_stored_event(
                    row,
                    session_id=session_id,
                    revision=revision_number,
                    sequence=sequence,
                )
                result.append({
                    "id": row["id"],
                    "turn_id": row["turn_id"],
                    "revision": row["revision"],
                    "sequence": row["sequence"],
                    "type": row["event_type"],
                    "event": event.model_dump(mode="json"),
                    "created_at": row["created_at"],
                })
        return result

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        """Return validated action/event/narration history in revision order."""

        with closing(connect(self.db_path)) as connection:
            session = connection.execute(
                "SELECT current_revision FROM world_game_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise GameNotFound("Session 不存在")
            rows = list(connection.execute(
                """
                SELECT * FROM world_game_turns
                WHERE session_id=? ORDER BY revision,id
                """,
                (session_id,),
            ))
            current_revision = int(session["current_revision"])
            if len(rows) != current_revision:
                raise GameDataError("turn 日志与当前 revision 数量不一致")

            result: list[dict[str, Any]] = []
            for expected_revision, row in enumerate(rows, start=1):
                if int(row["revision"]) != expected_revision:
                    raise GameDataError(
                        f"turn 日志缺少或重复 revision {expected_revision}"
                    )
                action = validated_stored_turn(row)
                committed = self._committed_action_payload(
                    connection,
                    row,
                    idempotent_replay=False,
                )
                result.append({
                    "id": row["id"],
                    "session_id": session_id,
                    "revision": expected_revision,
                    "actor": action.actor.model_dump(mode="json"),
                    "intent": action.intent.model_dump(mode="json"),
                    "content": action.content,
                    "events": committed["events"],
                    "event_count": len(committed["events"]),
                    "narration": committed.get("narration"),
                    "created_at": row["created_at"],
                })
        return result

    def create_save(
        self,
        session_id: str,
        body: SaveCreateIn,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        request_hash = json_hash(body)
        with closing(connect(self.db_path)) as connection:
            try:
                transaction_started = False
                if idempotency_key is not None:
                    connection.execute("BEGIN IMMEDIATE")
                    transaction_started = True
                    existing_id = self._operation_result_id(
                        connection,
                        "create_save",
                        session_id,
                        idempotency_key,
                        request_hash,
                    )
                    if existing_id is not None:
                        existing = connection.execute(
                            """
                            SELECT id,session_id,revision,name,state_hash,created_at
                            FROM world_game_saves WHERE id=? AND session_id=?
                            """,
                            (existing_id, session_id),
                        ).fetchone()
                        if existing is None:
                            raise GameDataError("存档幂等记录指向不存在的结果")
                        connection.commit()
                        return dict(existing)

                if transaction_started:
                    session_row, _state = self._load_current_session_state(
                        connection,
                        session_id,
                    )
                    current_revision = int(session_row["current_revision"])
                else:
                    current_revision = int(self.get_session(session_id)["current_revision"])
                revision = current_revision if body.revision is None else body.revision
                state = self.replay(session_id, revision)
                state_json = canonical_json(state)
                digest = state_digest(state)
                save_id = new_id("save")
                stamp = now_iso()
                if not transaction_started:
                    connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO world_game_saves VALUES(?,?,?,?,?,?,?)",
                    (save_id, session_id, revision, body.name, state_json, digest, stamp),
                )
                self._record_operation(
                    connection,
                    "create_save",
                    session_id,
                    idempotency_key,
                    request_hash,
                    save_id,
                    stamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "id": save_id,
            "session_id": session_id,
            "revision": revision,
            "name": body.name,
            "state_hash": digest,
            "created_at": stamp,
        }

    def list_saves(self, session_id: str) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            if not connection.execute(
                "SELECT 1 FROM world_game_sessions WHERE id=?", (session_id,)
            ).fetchone():
                raise GameNotFound("Session 不存在")
            rows = connection.execute(
                """
                SELECT id,session_id,revision,name,state_hash,created_at
                FROM world_game_saves WHERE session_id=? ORDER BY created_at DESC,id DESC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def branch_from_save(
        self,
        session_id: str,
        save_id: str,
        body: BranchCreateIn,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        request_hash = json_hash(body)
        operation_scope = f"{session_id}:{save_id}"
        with closing(connect(self.db_path)) as connection:
            try:
                transaction_started = False
                if idempotency_key is not None:
                    connection.execute("BEGIN IMMEDIATE")
                    transaction_started = True
                    existing_id = self._operation_result_id(
                        connection,
                        "branch_from_save",
                        operation_scope,
                        idempotency_key,
                        request_hash,
                    )
                    if existing_id is not None:
                        connection.commit()
                        return self.get_session(existing_id)

                save = connection.execute(
                    "SELECT * FROM world_game_saves WHERE id=? AND session_id=?",
                    (save_id, session_id),
                ).fetchone()
                parent = connection.execute(
                    "SELECT * FROM world_game_sessions WHERE id=?", (session_id,)
                ).fetchone()
                if not save or not parent:
                    raise GameNotFound("存档或所属 Session 不存在")
                branch_session_id = new_id("session")
                stamp = now_iso()
                saved_state = WorldState.model_validate_json(save["state_json"])
                if state_digest(saved_state) != save["state_hash"]:
                    raise GameDataError("存档内容与哈希不一致")
                saved_state.session_id = branch_session_id
                saved_state.revision = 0
                validate_world_state(saved_state)
                state_json = canonical_json(saved_state)
                digest = state_digest(saved_state)
                if not transaction_started:
                    connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO world_game_sessions(
                      id,world_id,world_version_id,branch_name,parent_session_id,parent_revision,
                      player_id,current_revision,initial_state_json,current_state_json,state_hash,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,0,?,?,?,?,?)
                    """,
                    (
                        branch_session_id, parent["world_id"], parent["world_version_id"],
                        body.branch_name, session_id, save["revision"], parent["player_id"],
                        state_json, state_json, digest, stamp, stamp,
                    ),
                )
                player = connection.execute(
                    "SELECT actor_id,display_name FROM world_game_players WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                connection.execute(
                    "INSERT INTO world_game_players VALUES(?,?,?,?)",
                    (branch_session_id, player["actor_id"], player["display_name"], stamp),
                )
                for row in connection.execute(
                    "SELECT actor_id,definition_id FROM world_game_npcs WHERE session_id=?",
                    (session_id,),
                ):
                    connection.execute(
                        "INSERT INTO world_game_npcs VALUES(?,?,?,?)",
                        (branch_session_id, row["actor_id"], row["definition_id"], stamp),
                    )
                ai_character_rows = list(connection.execute(
                    """
                    SELECT actor_id,persona_id,provider_id,persona_snapshot_json,config_json
                    FROM world_game_ai_characters WHERE session_id=?
                    """,
                    (session_id,),
                ))
                if {row["actor_id"] for row in ai_character_rows} != set(saved_state.ai_characters):
                    raise GameDataError("存档中的 AICharacter 与 Session 运行快照集合不一致")
                for row in ai_character_rows:
                    runtime = validated_stored_ai_character_runtime(row)
                    resident = saved_state.ai_characters[row["actor_id"]]
                    if (
                        resident.persona_id != runtime.persona_id
                        or resident.provider_id != runtime.provider_id
                        or resident.runtime_hash != ai_character_runtime_hash(runtime)
                    ):
                        raise GameDataError(
                            f"AICharacter {resident.id} 的存档身份与运行快照不一致"
                        )
                    connection.execute(
                        """
                        INSERT INTO world_game_ai_characters(
                          session_id,actor_id,persona_id,provider_id,
                          persona_snapshot_json,config_json,created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            branch_session_id, row["actor_id"], row["persona_id"], row["provider_id"],
                            row["persona_snapshot_json"], row["config_json"], stamp,
                        ),
                    )
                connection.execute(
                    "INSERT INTO world_game_snapshots VALUES(?,?,?,?,?)",
                    (branch_session_id, 0, state_json, digest, stamp),
                )
                self._record_operation(
                    connection,
                    "branch_from_save",
                    operation_scope,
                    idempotency_key,
                    request_hash,
                    branch_session_id,
                    stamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_session(branch_session_id)
