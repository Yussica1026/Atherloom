from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

from backend.importers.api_models import ImportConversationPlan
from backend.importers.models import CanonicalImportBatch
from backend.importers.schema import DbPath, connect, init_schema


class ImportBatchNotFound(LookupError):
    pass


class ImportBatchConflict(ValueError):
    pass


class ImportBatchDataError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ImportBatchRepository:
    def __init__(self, db_path: DbPath):
        self.db_path = db_path

    def init_schema(self) -> None:
        init_schema(self.db_path)

    def create_preview(
        self,
        batch_id: str,
        canonical: CanonicalImportBatch,
        *,
        conversation_count: int,
        message_count: int,
        created_at: str,
    ) -> dict[str, Any]:
        canonical_payload = canonical.model_dump(mode="json")
        warnings = [item.model_dump(mode="json") for item in canonical.warnings]
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO import_batches(
                  id,format_id,platform,source_name,status,canonical_json,
                  warnings_json,conversation_count,message_count,created_at
                ) VALUES(?,?,?,?, 'previewed',?,?,?,?,?)
                """,
                (
                    batch_id,
                    canonical.format_id,
                    canonical.platform,
                    canonical.provenance.source_name,
                    _json(canonical_payload),
                    _json(warnings),
                    conversation_count,
                    message_count,
                    created_at,
                ),
            )
            connection.commit()
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT * FROM import_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if row is None:
                raise ImportBatchNotFound("导入批次不存在")
            conversations = connection.execute(
                """
                SELECT imported.conversation_id,
                       imported.source_conversation_id,
                       imported.canonical_conversation_id,
                       imported.imported_message_count,
                       imported.created_at,
                       conversation.persona_id,
                       conversation.archived
                FROM import_conversation_provenance AS imported
                LEFT JOIN conversations AS conversation
                  ON conversation.id=imported.conversation_id
                WHERE imported.batch_id=?
                ORDER BY imported.created_at,imported.conversation_id
                """,
                (batch_id,),
            ).fetchall()
        return self._batch_payload(row, conversations)

    def list_batches(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT * FROM import_batches
                ORDER BY created_at DESC,id DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._batch_payload(row, ()) for row in rows]

    def load_canonical(self, batch_id: str) -> tuple[dict[str, Any], CanonicalImportBatch]:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT * FROM import_batches WHERE id=?", (batch_id,)
            ).fetchone()
        if row is None:
            raise ImportBatchNotFound("导入批次不存在")
        try:
            canonical = CanonicalImportBatch.model_validate(json.loads(row["canonical_json"]))
        except (json.JSONDecodeError, ValueError, TypeError) as error:
            raise ImportBatchDataError("导入批次的 canonical 数据无法读取") from error
        return dict(row), canonical

    def committed_conversation_ids_by_source(
        self,
        platform: str,
        source_conversation_ids: set[str],
        *,
        exclude_batch_id: str | None = None,
    ) -> dict[str, list[str]]:
        source_ids = sorted(
            {
                source_conversation_id.strip()
                for source_conversation_id in source_conversation_ids
                if source_conversation_id.strip()
            }
        )
        if not source_ids:
            return {}

        matches: dict[str, list[str]] = {}
        with closing(connect(self.db_path)) as connection:
            # Keep each statement below SQLite's common 999-parameter limit.
            for start in range(0, len(source_ids), 900):
                chunk = source_ids[start : start + 900]
                placeholders = ",".join("?" for _ in chunk)
                parameters: list[str] = [platform, *chunk]
                exclude_clause = ""
                if exclude_batch_id is not None:
                    exclude_clause = " AND imported.batch_id<>?"
                    parameters.append(exclude_batch_id)
                rows = connection.execute(
                    f"""
                    SELECT imported.source_conversation_id,
                           imported.conversation_id
                    FROM import_conversation_provenance AS imported
                    JOIN import_batches AS batch ON batch.id=imported.batch_id
                    WHERE batch.status='committed'
                      AND batch.platform=?
                      AND imported.source_conversation_id IN ({placeholders})
                      {exclude_clause}
                    ORDER BY imported.created_at,imported.conversation_id
                    """,
                    parameters,
                ).fetchall()
                for row in rows:
                    conversation_ids = matches.setdefault(
                        row["source_conversation_id"], []
                    )
                    if row["conversation_id"] not in conversation_ids:
                        conversation_ids.append(row["conversation_id"])
        return matches

    def commit_batch(
        self,
        batch_id: str,
        plans: tuple[ImportConversationPlan, ...],
        *,
        committed_at: str,
        required_persona_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM import_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ImportBatchNotFound("导入批次不存在")
            if row["status"] == "committed":
                connection.rollback()
                return self.get_batch(batch_id)
            if row["status"] != "previewed":
                connection.rollback()
                raise ImportBatchConflict("只有预览完成的批次可以提交")
            if required_persona_ids:
                placeholders = ",".join("?" for _ in required_persona_ids)
                existing_personas = {
                    item["id"]
                    for item in connection.execute(
                        f"SELECT id FROM personas WHERE id IN ({placeholders})",
                        required_persona_ids,
                    )
                }
                missing_personas = [
                    persona_id
                    for persona_id in required_persona_ids
                    if persona_id not in existing_personas
                ]
                if missing_personas:
                    connection.rollback()
                    raise ImportBatchConflict(
                        "Persona 不存在：" + ", ".join(missing_personas)
                    )
            try:
                for plan in plans:
                    connection.execute(
                        """
                        INSERT INTO conversations(
                          id,title,provider_id,persona_id,summary,created_at,updated_at,
                          pinned,starred,archived
                        ) VALUES(?,?,NULL,?,'',?,?,0,0,1)
                        """,
                        (
                            plan.id,
                            plan.title,
                            plan.persona_id,
                            plan.created_at,
                            plan.updated_at,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO import_conversation_provenance(
                          batch_id,conversation_id,source_conversation_id,
                          canonical_conversation_id,provenance_json,
                          imported_message_count,created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            batch_id,
                            plan.id,
                            plan.source_conversation_id,
                            plan.canonical_conversation_id,
                            plan.provenance_json,
                            len(plan.messages),
                            committed_at,
                        ),
                    )
                    for message in plan.messages:
                        connection.execute(
                            """
                            INSERT INTO messages(
                              id,conversation_id,role,content,provider_id,model,
                              created_at,reasoning,parent_message_id
                            ) VALUES(?,?,?,?,NULL,NULL,?,?,?)
                            """,
                            (
                                message.id,
                                plan.id,
                                message.role,
                                message.content,
                                message.created_at,
                                message.reasoning,
                                message.parent_message_id,
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO import_message_provenance(
                              batch_id,message_id,conversation_id,
                              source_conversation_id,canonical_conversation_id,
                              source_node_id,source_message_id,provenance_json,created_at
                            ) VALUES(?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                batch_id,
                                message.id,
                                plan.id,
                                message.source_conversation_id,
                                message.canonical_conversation_id,
                                message.source_node_id,
                                message.source_message_id,
                                message.provenance_json,
                                committed_at,
                            ),
                        )
                    for parent_message_id, assistant_message_id in plan.selections:
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO message_selections(
                              conversation_id,parent_message_id,assistant_message_id
                            ) VALUES(?,?,?)
                            """,
                            (plan.id, parent_message_id, assistant_message_id),
                        )
                connection.execute(
                    """
                    UPDATE import_batches
                    SET status='committed',committed_at=? WHERE id=?
                    """,
                    (committed_at, batch_id),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise ImportBatchConflict("导入提交与现有数据冲突") from error
            except Exception:
                connection.rollback()
                raise
        return self.get_batch(batch_id)

    def rollback_batch(self, batch_id: str, *, rolled_back_at: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT * FROM import_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is None:
                connection.rollback()
                raise ImportBatchNotFound("导入批次不存在")
            if batch["status"] == "rolled_back":
                connection.rollback()
                return self.get_batch(batch_id)
            if batch["status"] == "previewed":
                connection.execute(
                    """
                    UPDATE import_batches
                    SET status='rolled_back',rolled_back_at=? WHERE id=?
                    """,
                    (rolled_back_at, batch_id),
                )
                connection.commit()
                return self.get_batch(batch_id)
            provenance = connection.execute(
                """
                SELECT conversation_id,created_by_batch
                FROM import_conversation_provenance
                WHERE batch_id=? ORDER BY conversation_id
                """,
                (batch_id,),
            ).fetchall()
            blocked: list[str] = []
            for item in provenance:
                if not item["created_by_batch"]:
                    blocked.append(item["conversation_id"])
                    continue
                native = connection.execute(
                    """
                    SELECT 1
                    FROM messages AS message
                    LEFT JOIN import_message_provenance AS imported
                      ON imported.batch_id=? AND imported.message_id=message.id
                    WHERE message.conversation_id=? AND imported.message_id IS NULL
                    LIMIT 1
                    """,
                    (batch_id, item["conversation_id"]),
                ).fetchone()
                if native is not None:
                    blocked.append(item["conversation_id"])
            if blocked:
                connection.rollback()
                raise ImportBatchConflict(
                    "导入会话已经追加原生消息，不能回滚：" + ", ".join(blocked)
                )
            conversation_ids = [item["conversation_id"] for item in provenance]
            message_rows = connection.execute(
                """
                SELECT message_id FROM import_message_provenance WHERE batch_id=?
                """,
                (batch_id,),
            ).fetchall()
            message_ids = [item["message_id"] for item in message_rows]
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                connection.execute(
                    f"DELETE FROM message_trash WHERE message_id IN ({placeholders})",
                    message_ids,
                )
                connection.execute(
                    f"DELETE FROM message_tool_events WHERE message_id IN ({placeholders})",
                    message_ids,
                )
                connection.execute(
                    f"UPDATE memories SET source_message_id=NULL WHERE source_message_id IN ({placeholders})",
                    message_ids,
                )
                connection.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    message_ids,
                )
            for conversation_id in conversation_ids:
                connection.execute(
                    "DELETE FROM message_selections WHERE conversation_id=?",
                    (conversation_id,),
                )
                connection.execute(
                    "DELETE FROM summary_versions WHERE conversation_id=?",
                    (conversation_id,),
                )
                connection.execute(
                    "DELETE FROM conversation_continuity WHERE conversation_id=?",
                    (conversation_id,),
                )
                connection.execute(
                    "DELETE FROM timeline_archived_messages WHERE conversation_id=?",
                    (conversation_id,),
                )
                connection.execute(
                    "UPDATE memories SET source_conversation_id=NULL WHERE source_conversation_id=?",
                    (conversation_id,),
                )
                connection.execute(
                    "UPDATE mcp_audit SET conversation_id=NULL WHERE conversation_id=?",
                    (conversation_id,),
                )
                connection.execute(
                    "DELETE FROM conversations WHERE id=?", (conversation_id,)
                )
            connection.execute(
                """
                UPDATE import_batches
                SET status='rolled_back',rolled_back_at=? WHERE id=?
                """,
                (rolled_back_at, batch_id),
            )
            connection.commit()
        return self.get_batch(batch_id)

    def conversation_provenance(self, conversation_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            conversation = connection.execute(
                """
                SELECT * FROM import_conversation_provenance
                WHERE conversation_id=?
                """,
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise ImportBatchNotFound("该会话没有外部导入来源")
            messages = connection.execute(
                """
                SELECT * FROM import_message_provenance
                WHERE conversation_id=? ORDER BY created_at,message_id
                """,
                (conversation_id,),
            ).fetchall()
            batch = connection.execute(
                "SELECT * FROM import_batches WHERE id=?",
                (conversation["batch_id"],),
            ).fetchone()
        if batch is None:
            raise ImportBatchDataError("导入来源批次缺失")
        return {
            "batch": self._batch_payload(batch, ()),
            "conversation": self._provenance_payload(conversation),
            "messages": [self._provenance_payload(item) for item in messages],
        }

    @staticmethod
    def _provenance_payload(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        try:
            payload["provenance"] = json.loads(payload.pop("provenance_json"))
        except (json.JSONDecodeError, TypeError) as error:
            raise ImportBatchDataError("导入 provenance 无法读取") from error
        return payload

    @staticmethod
    def _batch_payload(row: sqlite3.Row, conversations: Any) -> dict[str, Any]:
        try:
            warnings = json.loads(row["warnings_json"] or "[]")
        except (json.JSONDecodeError, TypeError) as error:
            raise ImportBatchDataError("导入批次 warnings 无法读取") from error
        return {
            "id": row["id"],
            "format_id": row["format_id"],
            "platform": row["platform"],
            "source_name": row["source_name"],
            "status": row["status"],
            "conversation_count": row["conversation_count"],
            "message_count": row["message_count"],
            "warnings": warnings,
            "created_at": row["created_at"],
            "committed_at": row["committed_at"],
            "rolled_back_at": row["rolled_back_at"],
            "defaults": {
                "archived": True,
                "provider_id": None,
                "persona_id": None,
                "memories_created": 0,
            },
            "conversations": [dict(item) for item in conversations],
            "created_conversation_ids": [
                item["conversation_id"] for item in conversations
            ],
        }
