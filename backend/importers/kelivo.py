from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from io import BytesIO
from zipfile import BadZipFile, ZipFile
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .common import (
    as_list,
    as_mapping,
    finish_graph,
    normalize_role,
    normalize_timestamp,
    source_id,
    unique_id,
    unknown_fields,
)
from .models import (
    CanonicalContentBlock,
    CanonicalConversation,
    CanonicalImportBatch,
    CanonicalMessage,
    CanonicalMessageNode,
    CanonicalParticipant,
    ImportWarning,
    SourceProvenance,
)
from .registry import DetectionResult, ImporterError


_OPENING_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_IMAGE_MARKER = re.compile(r"^\[image:(.*)\]$")
_FILE_MARKER = re.compile(r"^\[file:([^|\]]+)\|([^|\]]+)\|([^|\]]*)\]$")


class KelivoSQLiteBackupUnsupported(ImporterError):
    """A Kelivo SQLite backup whose published schema is not supported."""


class KelivoZipPayloadUnsupported(ImporterError):
    """A Kelivo ZIP without a supported root JSON conversation payload."""


class KelivoBackupImporter:
    """Parse Kelivo's JSON chat backup payload (normally ``chats.json``)."""

    format_id = "kelivo.chats_backup"
    platform = "kelivo"
    aliases = ("kelivo-backup", "kelivo-chats", "kelivo-session")
    file_extensions = (".json", ".zip")
    media_types = ("application/json", "application/zip")

    _zip_json_entries = ("session.json", "chats.json")
    _sqlite_schema_version = 1
    _sqlite_required_columns = {
        "conversation_rows": {
            "id", "title", "created_at", "updated_at", "is_pinned", "assistant_id",
            "truncate_index", "version_selections_json", "summary",
            "last_summarized_message_count", "chat_suggestions_json",
            "injected_memory_hash", "last_memory_extracted_order",
        },
        "message_rows": {
            "id", "conversation_id", "role", "timestamp", "model_id", "provider_id",
            "total_tokens", "is_streaming", "reasoning_start_at", "reasoning_finished_at",
            "translation", "reasoning_segments_json", "group_id", "version",
            "prompt_tokens", "completion_tokens", "cached_tokens", "duration_ms",
            "message_order",
        },
        "conversation_mcp_server_rows": {"conversation_id", "server_id", "ordinal"},
        "message_part_rows": {
            "part_id", "conversation_id", "revision_id", "ordinal", "kind", "payload",
            "created_at", "updated_at",
        },
        "provider_artifact_rows": {
            "conversation_id", "revision_id", "kind", "payload", "created_at", "updated_at",
        },
    }

    _top_keys = {
        "version", "conversations", "conversation", "messages", "toolEvents",
        "geminiThoughtSigs", "attachments",
    }
    _conversation_keys = {
        "id", "title", "createdAt", "updatedAt", "messageIds", "isPinned",
        "mcpServerIds", "assistantId", "truncateIndex", "versionSelections", "summary",
        "lastSummarizedMessageCount", "chatSuggestions", "injectedMemoryHash",
        "lastMemoryExtractedOrder",
    }
    _message_keys = {
        "id", "role", "content", "parts", "timestamp", "modelId", "providerId",
        "totalTokens", "conversationId", "isStreaming", "reasoningText",
        "reasoningStartAt", "reasoningFinishedAt", "translation",
        "reasoningSegmentsJson", "groupId", "version", "promptTokens",
        "completionTokens", "cachedTokens", "durationMs",
    }

    def detect(self, payload: Any, source_name: str | None = None) -> DetectionResult | None:
        if isinstance(payload, bytes) and self._looks_like_zip(payload):
            return self._detect_zip(payload)
        return self._detect_json(payload)

    def _detect_json(self, payload: Any) -> DetectionResult | None:
        raw = as_mapping(payload)
        if raw is None:
            return None
        has_messages = isinstance(raw.get("messages"), list)
        full_shape = isinstance(raw.get("conversations"), list) and has_messages
        session_shape = isinstance(raw.get("conversation"), Mapping) and has_messages
        if not (full_shape or session_shape):
            return None
        exact_artifacts = "toolEvents" in raw or "geminiThoughtSigs" in raw
        if exact_artifacts:
            return DetectionResult(
                self.format_id,
                self.platform,
                0.995,
                ("messages", "toolEvents/geminiThoughtSigs"),
            )
        conversations = raw.get("conversations") if full_shape else [raw.get("conversation")]
        conversation_sample = next((item for item in conversations if isinstance(item, Mapping)), {})
        message_sample = next((item for item in raw["messages"] if isinstance(item, Mapping)), {})
        if "messageIds" in conversation_sample and "conversationId" in message_sample:
            return DetectionResult(
                self.format_id,
                self.platform,
                0.98,
                ("conversation.messageIds", "message.conversationId"),
            )
        return None

    def parse(self, payload: Any, source_name: str | None = None) -> CanonicalImportBatch:
        container_metadata: dict[str, Any] | None = None
        if isinstance(payload, bytes) and self._looks_like_zip(payload):
            payload, container_metadata = self._json_payload_from_zip(payload)
        raw = as_mapping(payload)
        if raw is None:
            raise ImporterError("Kelivo chat backup must be a JSON object")
        conversations_value = raw.get("conversations")
        if isinstance(conversations_value, list):
            conversation_values = conversations_value
        elif isinstance(raw.get("conversation"), Mapping):
            conversation_values = [raw["conversation"]]
        else:
            raise ImporterError("Kelivo chat backup is missing conversations")
        if not isinstance(raw.get("messages"), list):
            raise ImporterError("Kelivo chat backup is missing messages")

        warnings: list[ImportWarning] = []
        invalid_root_fields: dict[str, Any] = {}
        invalid_message_entries: list[dict[str, Any]] = []
        invalid_conversation_entries: list[dict[str, Any]] = []

        # SQLite adaptation records row-level defects as data so parsing can
        # finish the rest of the official backup without hiding the bad row.
        sqlite_source = as_mapping(raw.get("sqliteSource"))
        if container_metadata is not None and "sqlite" in container_metadata and sqlite_source:
            for issue in as_list(sqlite_source.get("warnings")):
                issue_mapping = as_mapping(issue)
                if issue_mapping is None:
                    continue
                code = issue_mapping.get("code")
                message = issue_mapping.get("message")
                if not isinstance(code, str) or not isinstance(message, str):
                    continue
                warnings.append(ImportWarning(
                    code=code,
                    message=message,
                    conversation_id=source_id(issue_mapping.get("conversation_id")),
                    node_id=source_id(issue_mapping.get("node_id")),
                ))

        message_values = raw["messages"]
        valid_messages: list[tuple[int, dict[str, Any]]] = []
        for index, value in enumerate(message_values):
            message = as_mapping(value)
            if message is None:
                warnings.append(ImportWarning(
                    code="invalid_message",
                    message=f"Skipped non-object message at index {index}",
                ))
                invalid_message_entries.append({
                    "source_index": index,
                    "value": deepcopy(value),
                })
                continue
            valid_messages.append((index, message))

        tool_events_value = raw.get("toolEvents")
        tool_events = as_mapping(tool_events_value)
        if "toolEvents" in raw and tool_events is None:
            warnings.append(ImportWarning(
                code="invalid_tool_events",
                message="Preserved toolEvents because the field is not an object",
            ))
            invalid_root_fields["toolEvents"] = deepcopy(tool_events_value)
            tool_events = {}

        thought_signatures_value = raw.get("geminiThoughtSigs")
        thought_signatures = as_mapping(thought_signatures_value)
        if "geminiThoughtSigs" in raw and thought_signatures is None:
            warnings.append(ImportWarning(
                code="invalid_thought_signatures",
                message="Preserved geminiThoughtSigs because the field is not an object",
            ))
            invalid_root_fields["geminiThoughtSigs"] = deepcopy(thought_signatures_value)
            thought_signatures = {}

        attachments_value = raw.get("attachments")
        attachments = as_list(attachments_value)
        if "attachments" in raw and not isinstance(attachments_value, (list, tuple)):
            warnings.append(ImportWarning(
                code="invalid_attachments",
                message="Preserved attachments because the field is not a list",
            ))
            invalid_root_fields["attachments"] = deepcopy(attachments_value)
        by_id: dict[str, tuple[int, dict[str, Any]]] = {}
        by_conversation: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for index, message in valid_messages:
            message_id = source_id(message.get("id"))
            if message_id and message_id not in by_id:
                by_id[message_id] = (index, message)
            conversation_id = source_id(message.get("conversationId"))
            if conversation_id:
                by_conversation[conversation_id].append((index, message))

        conversations: list[CanonicalConversation] = []
        consumed_message_indexes: set[int] = set()
        known_conversation_ids: set[str] = set()
        for conversation_index, value in enumerate(conversation_values):
            conversation_raw = as_mapping(value)
            if conversation_raw is None:
                warnings.append(ImportWarning(
                    code="invalid_conversation",
                    message=f"Skipped non-object conversation at index {conversation_index}",
                ))
                invalid_conversation_entries.append({
                    "source_index": conversation_index,
                    "value": deepcopy(value),
                })
                continue
            conversation_id = source_id(conversation_raw.get("id")) or f"kelivo-conversation-{conversation_index + 1}"
            known_conversation_ids.add(conversation_id)
            ordered: list[tuple[int, dict[str, Any]]] = []
            seen_indexes: set[int] = set()
            for message_id_value in as_list(conversation_raw.get("messageIds")):
                message_id = source_id(message_id_value)
                entry = by_id.get(message_id or "")
                if entry and entry[0] not in seen_indexes:
                    ordered.append(entry)
                    seen_indexes.add(entry[0])
                elif message_id:
                    warnings.append(ImportWarning(
                        code="missing_message_reference",
                        message=f"Conversation references missing message {message_id}",
                        conversation_id=conversation_id,
                    ))
            for entry in by_conversation.get(conversation_id, []):
                if entry[0] not in seen_indexes:
                    ordered.append(entry)
                    seen_indexes.add(entry[0])
            consumed_message_indexes.update(seen_indexes)
            conversations.append(self._parse_conversation(
                conversation_raw,
                conversation_index,
                ordered,
                tool_events,
                thought_signatures,
                attachments,
                source_name,
                warnings,
            ))

        # Keep otherwise valid messages whose conversation record is absent.
        orphan_groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        unmatched_without_conversation: list[Any] = []
        for index, message in valid_messages:
            if index in consumed_message_indexes:
                continue
            conversation_id = source_id(message.get("conversationId"))
            if conversation_id and conversation_id not in known_conversation_ids:
                orphan_groups[conversation_id].append((index, message))
            else:
                unmatched_without_conversation.append(deepcopy(message))
        for orphan_index, (conversation_id, entries) in enumerate(orphan_groups.items()):
            warnings.append(ImportWarning(
                code="missing_conversation",
                message="Messages referenced a conversation absent from the backup",
                conversation_id=conversation_id,
            ))
            synthetic = {
                "id": conversation_id,
                "title": "",
                "messageIds": [source_id(message.get("id")) for _, message in entries],
            }
            conversations.append(self._parse_conversation(
                synthetic,
                len(conversation_values) + orphan_index,
                entries,
                tool_events,
                thought_signatures,
                [],
                source_name,
                warnings,
            ))

        matched_message_ids = {
            node.message.provenance.message_id
            for conversation in conversations
            for node in conversation.nodes
            if node.message and node.message.provenance.message_id
        }
        unmatched_tool_events = {
            key: deepcopy(value) for key, value in tool_events.items() if key not in matched_message_ids
        }
        unmatched_signatures = {
            key: deepcopy(value) for key, value in thought_signatures.items() if key not in matched_message_ids
        }
        batch_extra = unknown_fields(raw, self._top_keys)
        batch_extra.update({
            "version": deepcopy(raw.get("version")),
            "unmatched_messages": unmatched_without_conversation,
            "unmatched_tool_events": unmatched_tool_events,
            "unmatched_thought_signatures": unmatched_signatures,
        })
        if invalid_root_fields:
            batch_extra["invalid_root_fields"] = invalid_root_fields
        if invalid_message_entries:
            batch_extra["invalid_message_entries"] = invalid_message_entries
        if invalid_conversation_entries:
            batch_extra["invalid_conversation_entries"] = invalid_conversation_entries
        if container_metadata is not None:
            batch_extra["container"] = container_metadata
        return CanonicalImportBatch(
            format_id=self.format_id,
            platform=self.platform,
            conversations=conversations,
            warnings=warnings,
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                extra=batch_extra,
            ),
        )

    @staticmethod
    def _looks_like_zip(payload: bytes) -> bool:
        return payload.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))

    def _detect_zip(self, payload: bytes) -> DetectionResult | None:
        try:
            with ZipFile(BytesIO(payload)) as archive:
                names = set(archive.namelist())
                for entry_name in self._zip_json_entries:
                    if entry_name not in names:
                        continue
                    decoded = self._read_zip_json(archive, entry_name)
                    if decoded is not None and self._detect_json(decoded) is not None:
                        return DetectionResult(
                            self.format_id,
                            self.platform,
                            0.999,
                            ("zip", entry_name),
                        )
                manifest = self._read_zip_json(archive, "manifest.json") if "manifest.json" in names else None
                if self._is_current_sqlite_manifest(manifest):
                    return DetectionResult(
                        self.format_id,
                        self.platform,
                        0.995,
                        ("zip", "manifest.json", "kelivo-backup-v2-sqlite"),
                    )
        except (BadZipFile, OSError):
            return None
        return None

    def _json_payload_from_zip(self, payload: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            with ZipFile(BytesIO(payload)) as archive:
                names = archive.namelist()
                name_set = set(names)
                invalid_json_entries: list[str] = []
                for entry_name in self._zip_json_entries:
                    if entry_name not in name_set:
                        continue
                    decoded = self._read_zip_json(archive, entry_name)
                    if decoded is None or self._detect_json(decoded) is None:
                        invalid_json_entries.append(entry_name)
                        continue
                    return decoded, self._zip_container_metadata(names, json_entry=entry_name)

                manifest = self._read_zip_json(archive, "manifest.json") if "manifest.json" in name_set else None
                if self._is_current_sqlite_manifest(manifest):
                    database = as_mapping(manifest.get("database")) or {}
                    database_entry = database.get("entry")
                    if not isinstance(database_entry, str) or database_entry not in name_set:
                        raise KelivoZipPayloadUnsupported(
                            "Kelivo backup manifest references missing database/kelivo.db"
                        )
                    adapted, sqlite_metadata = self._json_payload_from_sqlite(
                        archive.read(database_entry),
                        manifest,
                    )
                    container = self._zip_container_metadata(
                        names,
                        manifest=manifest,
                        database_entry=database_entry,
                    )
                    container["sqlite"] = sqlite_metadata
                    return adapted, container
                if isinstance(manifest, Mapping) and manifest.get("format") == "kelivo-backup":
                    raise KelivoZipPayloadUnsupported(
                        "Kelivo ZIP has no supported root session.json or chats.json conversation payload"
                    )
                if invalid_json_entries:
                    entries = ", ".join(invalid_json_entries)
                    raise KelivoZipPayloadUnsupported(
                        f"Kelivo ZIP entries are not recognized conversation JSON payloads: {entries}"
                    )
        except (KelivoSQLiteBackupUnsupported, KelivoZipPayloadUnsupported):
            raise
        except (BadZipFile, OSError) as exc:
            raise ImporterError("Kelivo ZIP is not a readable archive") from exc
        raise KelivoZipPayloadUnsupported(
            "ZIP has no verified Kelivo root session.json or chats.json payload"
        )

    @staticmethod
    def _read_zip_json(archive: ZipFile, entry_name: str) -> dict[str, Any] | None:
        try:
            decoded = json.loads(archive.read(entry_name).decode("utf-8-sig"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            return None
        return as_mapping(decoded)

    @staticmethod
    def _is_current_sqlite_manifest(value: Any) -> bool:
        manifest = as_mapping(value)
        if manifest is None:
            return False
        database = as_mapping(manifest.get("database")) or {}
        return (
            manifest.get("format") == "kelivo-backup"
            and manifest.get("formatVersion") == 2
            and manifest.get("payloadKind") == "sqlite"
            and database.get("entry") == "database/kelivo.db"
        )

    @staticmethod
    def _zip_container_metadata(
        names: list[str],
        *,
        json_entry: str | None = None,
        manifest: Mapping[str, Any] | None = None,
        database_entry: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "type": "zip",
            "entries": list(names),
        }
        if json_entry is not None:
            metadata["json_entry"] = json_entry
        if manifest is not None:
            metadata["manifest"] = deepcopy(dict(manifest))
        if database_entry is not None:
            metadata["database_entry"] = database_entry
        excluded = {
            value for value in (json_entry, "manifest.json", "settings.json", database_entry)
            if value is not None
        }
        metadata["attachment_entries"] = [
            name for name in names
            if not name.endswith("/") and name not in excluded
        ]
        return metadata

    def _json_payload_from_sqlite(
        self,
        database_bytes: bytes,
        manifest: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Adapt Kelivo's published Drift v1 tables to its legacy JSON shape."""

        if not database_bytes.startswith(b"SQLite format 3\x00"):
            raise ImporterError("Kelivo database/kelivo.db is not a SQLite database")
        database_manifest = as_mapping(manifest.get("database")) or {}
        manifest_schema_version = database_manifest.get("schemaVersion")
        if (
            not isinstance(manifest_schema_version, int)
            or isinstance(manifest_schema_version, bool)
            or manifest_schema_version != self._sqlite_schema_version
        ):
            raise KelivoSQLiteBackupUnsupported(
                "Kelivo SQLite schema is unsupported: expected published schemaVersion 1"
            )

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.deserialize(database_bytes)
            connection.execute("PRAGMA query_only = ON")
            actual_schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if actual_schema_version != self._sqlite_schema_version:
                raise KelivoSQLiteBackupUnsupported(
                    "Kelivo SQLite PRAGMA user_version is unsupported: expected 1"
                )

            for table_name, required_columns in self._sqlite_required_columns.items():
                table = connection.execute(
                    "SELECT type FROM sqlite_master WHERE name = ?",
                    (table_name,),
                ).fetchone()
                if table is None or table["type"] != "table":
                    raise KelivoSQLiteBackupUnsupported(
                        f"Kelivo SQLite schema is missing official table {table_name}"
                    )
                actual_columns = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table_name})")
                }
                missing = sorted(required_columns - actual_columns)
                if missing:
                    raise KelivoSQLiteBackupUnsupported(
                        f"Kelivo SQLite table {table_name} is missing columns: {', '.join(missing)}"
                    )

            rows = {
                "conversation_rows": self._sqlite_rows(
                    connection,
                    "SELECT * FROM conversation_rows ORDER BY updated_at DESC, id",
                ),
                "message_rows": self._sqlite_rows(
                    connection,
                    "SELECT * FROM message_rows ORDER BY conversation_id, message_order, id",
                ),
                "conversation_mcp_server_rows": self._sqlite_rows(
                    connection,
                    "SELECT * FROM conversation_mcp_server_rows ORDER BY conversation_id, ordinal, server_id",
                ),
                "message_part_rows": self._sqlite_rows(
                    connection,
                    "SELECT * FROM message_part_rows ORDER BY revision_id, ordinal, part_id",
                ),
                "provider_artifact_rows": self._sqlite_rows(
                    connection,
                    "SELECT * FROM provider_artifact_rows ORDER BY revision_id, kind",
                ),
            }
        except KelivoSQLiteBackupUnsupported:
            raise
        except sqlite3.Error as exc:
            raise ImporterError("Kelivo database/kelivo.db could not be read") from exc
        finally:
            connection.close()

        payload = self._sqlite_rows_to_json(rows, actual_schema_version)
        return payload, {
            "schema_version": actual_schema_version,
            "conversation_count": len(rows["conversation_rows"]),
            "message_count": len(rows["message_rows"]),
            "source_tables": {
                table_name: len(table_rows)
                for table_name, table_rows in rows.items()
            },
        }

    @staticmethod
    def _sqlite_rows(connection: sqlite3.Connection, statement: str) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(statement)]

    def _sqlite_rows_to_json(
        self,
        rows: dict[str, list[dict[str, Any]]],
        schema_version: int,
    ) -> dict[str, Any]:
        sqlite_warnings: list[dict[str, Any]] = []
        message_rows = rows["message_rows"]
        message_ids = {
            str(row["id"])
            for row in message_rows
            if row.get("id") is not None
        }
        conversation_ids = {
            str(row["id"])
            for row in rows["conversation_rows"]
            if row.get("id") is not None
        }
        parts_by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
        orphan_parts: list[dict[str, Any]] = []
        for row in rows["message_part_rows"]:
            revision_id = str(row.get("revision_id") or "")
            if revision_id in message_ids:
                parts_by_message[revision_id].append(row)
            else:
                orphan_parts.append(deepcopy(row))
                sqlite_warnings.append({
                    "code": "orphan_message_part",
                    "message": "Preserved a SQLite message part whose parent message is missing",
                    "conversation_id": row.get("conversation_id"),
                    "node_id": row.get("revision_id"),
                })

        artifacts_by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
        orphan_artifacts: list[dict[str, Any]] = []
        for row in rows["provider_artifact_rows"]:
            revision_id = str(row.get("revision_id") or "")
            if revision_id in message_ids:
                artifacts_by_message[revision_id].append(row)
            else:
                orphan_artifacts.append(deepcopy(row))
                sqlite_warnings.append({
                    "code": "orphan_provider_artifact",
                    "message": "Preserved a SQLite provider artifact whose parent message is missing",
                    "conversation_id": row.get("conversation_id"),
                    "node_id": row.get("revision_id"),
                })

        mcp_servers: dict[str, list[str]] = defaultdict(list)
        orphan_mcp_servers: list[dict[str, Any]] = []
        for row in rows["conversation_mcp_server_rows"]:
            conversation_id = str(row.get("conversation_id") or "")
            server_id = row.get("server_id")
            if conversation_id in conversation_ids and isinstance(server_id, str):
                mcp_servers[conversation_id].append(server_id)
            else:
                orphan_mcp_servers.append(deepcopy(row))
                sqlite_warnings.append({
                    "code": "invalid_conversation_mcp_server",
                    "message": "Preserved a SQLite MCP link that cannot be attached to a conversation",
                    "conversation_id": row.get("conversation_id"),
                })

        messages: list[dict[str, Any]] = []
        messages_by_conversation: dict[str, list[str]] = defaultdict(list)
        tool_events: dict[str, list[dict[str, Any]]] = {}
        thought_signatures: dict[str, str] = {}
        invalid_tool_parts: list[dict[str, Any]] = []
        for row in message_rows:
            message_id = str(row.get("id") or "")
            conversation_id = str(row.get("conversation_id") or "")
            if conversation_id and message_id:
                messages_by_conversation[conversation_id].append(message_id)

            source_parts = parts_by_message.get(message_id, [])
            parts = [
                {
                    "kind": part.get("kind"),
                    "payload": part.get("payload"),
                    "sqliteRow": deepcopy(part),
                }
                for part in source_parts
            ]
            text_parts = [
                part.get("payload")
                for part in source_parts
                if part.get("kind") == "text" and isinstance(part.get("payload"), str)
            ]
            reasoning_parts = [
                part.get("payload")
                for part in source_parts
                if part.get("kind") == "reasoning" and isinstance(part.get("payload"), str)
            ]
            decoded_tool_events: list[dict[str, Any]] = []
            for part in source_parts:
                if part.get("kind") != "tool_call":
                    continue
                decoded = self._decode_sqlite_json(part.get("payload"))
                if isinstance(decoded, Mapping):
                    decoded_tool_events.append(deepcopy(dict(decoded)))
                else:
                    invalid_tool_parts.append(deepcopy(part))
                    sqlite_warnings.append({
                        "code": "invalid_tool_call_part",
                        "message": "Preserved a SQLite tool-call part whose payload is not a JSON object",
                        "conversation_id": row.get("conversation_id"),
                        "node_id": row.get("id"),
                    })
            if decoded_tool_events:
                tool_events[message_id] = decoded_tool_events

            message_artifacts = artifacts_by_message.get(message_id, [])
            for artifact in message_artifacts:
                if (
                    artifact.get("kind") == "gemini_thought_signature"
                    and isinstance(artifact.get("payload"), str)
                ):
                    thought_signatures[message_id] = artifact["payload"]
                elif artifact.get("kind") == "gemini_thought_signature":
                    sqlite_warnings.append({
                        "code": "invalid_thought_signature_artifact",
                        "message": "Preserved a SQLite thought-signature artifact with an invalid payload",
                        "conversation_id": row.get("conversation_id"),
                        "node_id": row.get("id"),
                    })

            messages.append({
                "id": row.get("id"),
                "role": row.get("role"),
                "content": "".join(text_parts),
                "parts": parts,
                "timestamp": self._sqlite_timestamp(row.get("timestamp")),
                "modelId": row.get("model_id"),
                "providerId": row.get("provider_id"),
                "totalTokens": row.get("total_tokens"),
                "conversationId": row.get("conversation_id"),
                "isStreaming": bool(row.get("is_streaming")),
                "reasoningText": "\n".join(reasoning_parts) if reasoning_parts else None,
                "reasoningStartAt": self._sqlite_timestamp(row.get("reasoning_start_at")),
                "reasoningFinishedAt": self._sqlite_timestamp(row.get("reasoning_finished_at")),
                "translation": row.get("translation"),
                "reasoningSegmentsJson": row.get("reasoning_segments_json"),
                "groupId": row.get("group_id"),
                "version": row.get("version"),
                "promptTokens": row.get("prompt_tokens"),
                "completionTokens": row.get("completion_tokens"),
                "cachedTokens": row.get("cached_tokens"),
                "durationMs": row.get("duration_ms"),
                "messageOrder": row.get("message_order"),
                "providerArtifacts": deepcopy(message_artifacts),
                "sqliteRow": deepcopy(row),
            })

        conversations = []
        for row in rows["conversation_rows"]:
            conversation_id = str(row.get("id") or "")
            raw_version_selections = row.get("version_selections_json")
            decoded_version_selections = self._decode_sqlite_json(raw_version_selections)
            if isinstance(decoded_version_selections, Mapping):
                version_selections = deepcopy(dict(decoded_version_selections))
            else:
                version_selections = {}
                sqlite_warnings.append({
                    "code": "invalid_version_selections",
                    "message": "Preserved a SQLite conversation row with invalid version selections",
                    "conversation_id": row.get("id"),
                })

            raw_chat_suggestions = row.get("chat_suggestions_json")
            decoded_chat_suggestions = self._decode_sqlite_json(raw_chat_suggestions)
            if isinstance(decoded_chat_suggestions, list):
                chat_suggestions = deepcopy(decoded_chat_suggestions)
            else:
                chat_suggestions = []
                sqlite_warnings.append({
                    "code": "invalid_chat_suggestions",
                    "message": "Preserved a SQLite conversation row with invalid chat suggestions",
                    "conversation_id": row.get("id"),
                })
            conversations.append({
                "id": row.get("id"),
                "title": row.get("title"),
                "createdAt": self._sqlite_timestamp(row.get("created_at")),
                "updatedAt": self._sqlite_timestamp(row.get("updated_at")),
                "messageIds": messages_by_conversation.get(conversation_id, []),
                "isPinned": bool(row.get("is_pinned")),
                "mcpServerIds": mcp_servers.get(conversation_id, []),
                "assistantId": row.get("assistant_id"),
                "truncateIndex": row.get("truncate_index"),
                "versionSelections": version_selections,
                "summary": row.get("summary"),
                "lastSummarizedMessageCount": row.get("last_summarized_message_count"),
                "chatSuggestions": chat_suggestions,
                "injectedMemoryHash": row.get("injected_memory_hash"),
                "lastMemoryExtractedOrder": row.get("last_memory_extracted_order"),
                "sqliteRow": deepcopy(row),
            })

        return {
            "version": 1,
            "conversations": conversations,
            "messages": messages,
            "toolEvents": tool_events,
            "geminiThoughtSigs": thought_signatures,
            "sqliteSource": {
                "schemaVersion": schema_version,
                "unmatchedMessageParts": orphan_parts,
                "unmatchedProviderArtifacts": orphan_artifacts,
                "unmatchedConversationMcpServers": orphan_mcp_servers,
                "unparsedToolCallParts": invalid_tool_parts,
                "warnings": sqlite_warnings,
            },
        }

    @staticmethod
    def _decode_sqlite_json(value: Any) -> Any:
        if not isinstance(value, str):
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _sqlite_timestamp(value: Any) -> str | None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        try:
            parsed = datetime.fromtimestamp(float(value) / 1_000_000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _source_integer(value: Any) -> int | None:
        """Return only finite, integral source numbers without lossy coercion."""

        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        return None

    def _parse_conversation(
        self,
        raw: dict[str, Any],
        source_index: int,
        message_entries: list[tuple[int, dict[str, Any]]],
        tool_events: dict[str, Any],
        thought_signatures: dict[str, Any],
        attachments: list[Any],
        source_name: str | None,
        warnings: list[ImportWarning],
    ) -> CanonicalConversation:
        conversation_id = source_id(raw.get("id")) or f"kelivo-conversation-{source_index + 1}"
        assistant_id = source_id(raw.get("assistantId"))
        participants: list[CanonicalParticipant] = []
        assistant: CanonicalParticipant | None = None
        if assistant_id:
            assistant = CanonicalParticipant(
                id=assistant_id,
                kind="assistant",
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=conversation_id,
                    extra={"source_field": "assistantId"},
                ),
            )
            participants.append(assistant)

        version_selections_value = raw.get("versionSelections")
        version_selections_raw = as_mapping(version_selections_value)
        if version_selections_raw is None:
            version_selections_raw = {}
            if version_selections_value is not None:
                warnings.append(ImportWarning(
                    code="invalid_version_selections",
                    message="Preserved versionSelections because the field is not an object",
                    conversation_id=conversation_id,
                ))
        version_selections: dict[str, int] = {}
        for key, value in version_selections_raw.items():
            parsed_version = self._source_integer(value)
            if parsed_version is None:
                warnings.append(ImportWarning(
                    code="invalid_version_selection",
                    message="Ignored a non-integral conversation version selection and preserved its raw value",
                    conversation_id=conversation_id,
                ))
                continue
            version_selections[key] = parsed_version
        parsed_nodes: list[CanonicalMessageNode] = []
        groups: dict[str, list[CanonicalMessageNode]] = {}
        group_order: list[str] = []
        used_node_ids: set[str] = set()
        for order, (original_index, message_raw) in enumerate(message_entries):
            source_message_id = source_id(message_raw.get("id")) or f"message-{original_index + 1}"
            node_id = unique_id(source_message_id, used_node_ids)
            group_id = source_id(message_raw.get("groupId")) or source_message_id
            if group_id not in groups:
                groups[group_id] = []
                group_order.append(group_id)
            message = self._parse_message(
                message_raw,
                node_id,
                conversation_id,
                source_name,
                original_index,
                tool_events.get(source_message_id),
                thought_signatures.get(source_message_id),
                assistant,
                warnings,
            )
            node = CanonicalMessageNode(
                id=node_id,
                message=message,
                source_order=order,
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=conversation_id,
                    node_id=source_message_id,
                    message_id=source_message_id,
                    branch_id=group_id,
                    source_index=original_index,
                    extra={
                        "group_id": group_id,
                        "version": deepcopy(message_raw.get("version")),
                    },
                ),
            )
            groups[group_id].append(node)
            parsed_nodes.append(node)

        previous_selected_id: str | None = None
        current_node_id: str | None = None
        for group_id in group_order:
            revisions = groups[group_id]
            selected_version = version_selections.get(group_id)
            selected = next(
                (
                    node for node in revisions
                    if node.message
                    and node.message.metadata.get("source_version") == selected_version
                ),
                revisions[-1],
            )
            for node in revisions:
                node.parent_id = previous_selected_id
                if node.message:
                    node.message.metadata["source_version_selected"] = node.id == selected.id
            previous_selected_id = selected.id
            current_node_id = selected.id

        roots, branches = finish_graph(parsed_nodes, current_node_id)
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        metadata = {
            "source_assistant_id": assistant_id,
            "source_message_ids": deepcopy(as_list(raw.get("messageIds"))),
            "version_selections": deepcopy(version_selections_raw),
            "attachments": deepcopy(attachments),
            "summary": deepcopy(raw.get("summary")),
        }
        return CanonicalConversation(
            id=conversation_id,
            title=title,
            created_at=normalize_timestamp(raw.get("createdAt")),
            updated_at=normalize_timestamp(raw.get("updatedAt")),
            current_node_id=current_node_id,
            root_node_ids=roots,
            nodes=parsed_nodes,
            branches=branches,
            participants=participants,
            metadata=metadata,
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=source_id(raw.get("id")),
                source_index=source_index,
                extra={
                    **unknown_fields(raw, self._conversation_keys),
                    "raw_created_at": deepcopy(raw.get("createdAt")),
                    "raw_updated_at": deepcopy(raw.get("updatedAt")),
                },
            ),
        )

    def _parse_message(
        self,
        raw: dict[str, Any],
        node_id: str,
        conversation_id: str,
        source_name: str | None,
        source_index: int,
        raw_tool_events: Any,
        raw_thought_signature: Any,
        assistant: CanonicalParticipant | None,
        warnings: list[ImportWarning],
    ) -> CanonicalMessage:
        source_message_id = source_id(raw.get("id"))
        if source_message_id is None:
            warnings.append(ImportWarning(
                code="missing_message_id",
                message="Assigned a stable fallback ID to a message with no usable ID",
                conversation_id=conversation_id,
                node_id=node_id,
            ))
        source_role = raw.get("role") if isinstance(raw.get("role"), str) else None
        blocks = self._message_blocks(
            raw,
            conversation_id,
            node_id,
            source_message_id or node_id,
            source_role,
            source_name,
            warnings,
        )
        tool_event_list = as_list(raw_tool_events)
        if raw_tool_events is not None and not isinstance(raw_tool_events, (list, tuple)):
            warnings.append(ImportWarning(
                code="invalid_message_tool_events",
                message="Preserved tool events because the message entry is not a list",
                conversation_id=conversation_id,
                node_id=node_id,
            ))
        for event_index, event in enumerate(tool_event_list):
            blocks.append(CanonicalContentBlock(
                kind="tool_event",
                raw=deepcopy(event),
                provenance=self._block_provenance(
                    conversation_id,
                    node_id,
                    source_message_id or node_id,
                    source_role,
                    source_name,
                    len(blocks),
                    {"tool_event_index": event_index},
                ),
            ))
        version_value = raw.get("version")
        parsed_source_version = self._source_integer(version_value)
        if version_value is not None and parsed_source_version is None:
            warnings.append(ImportWarning(
                code="invalid_message_version",
                message="Used version 0 for a non-integral message version and preserved its raw value",
                conversation_id=conversation_id,
                node_id=node_id,
            ))
        source_version = parsed_source_version if parsed_source_version is not None else 0
        metadata = {
            "source_group_id": source_id(raw.get("groupId")) or source_message_id,
            "source_version": source_version,
            "model_id": deepcopy(raw.get("modelId")),
            "provider_id": deepcopy(raw.get("providerId")),
            "is_streaming": deepcopy(raw.get("isStreaming")),
            "translation": deepcopy(raw.get("translation")),
            "reasoning_segments_json": deepcopy(raw.get("reasoningSegmentsJson")),
            "tool_events": deepcopy(tool_event_list),
            "thought_signature": deepcopy(raw_thought_signature),
        }
        role = normalize_role(source_role, {
            "user": "user", "assistant": "assistant", "system": "system", "tool": "tool",
        })
        return CanonicalMessage(
            id=source_message_id or f"{node_id}:message",
            role=role,
            created_at=normalize_timestamp(raw.get("timestamp")),
            updated_at=normalize_timestamp(raw.get("timestamp")),
            content=blocks,
            author=assistant if role == "assistant" else None,
            metadata=metadata,
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=conversation_id,
                node_id=node_id,
                message_id=source_message_id,
                source_role=source_role,
                source_index=source_index,
                extra={
                    **unknown_fields(raw, self._message_keys),
                    "raw_content": deepcopy(raw.get("content")),
                    "raw_parts": deepcopy(raw.get("parts")),
                    "raw_reasoning_text": deepcopy(raw.get("reasoningText")),
                    "raw_tool_events": deepcopy(raw_tool_events),
                    "raw_thought_signature": deepcopy(raw_thought_signature),
                },
            ),
        )

    def _message_blocks(
        self,
        raw: dict[str, Any],
        conversation_id: str,
        node_id: str,
        message_id: str,
        source_role: str | None,
        source_name: str | None,
        warnings: list[ImportWarning],
    ) -> list[CanonicalContentBlock]:
        parts = raw.get("parts")
        if isinstance(parts, list):
            blocks = []
            for index, value in enumerate(parts):
                part = as_mapping(value)
                if part is None:
                    kind, text, mime_type = "unknown", None, None
                    warnings.append(ImportWarning(
                        code="invalid_message_part",
                        message="Preserved a non-object message part as an unknown block",
                        conversation_id=conversation_id,
                        node_id=node_id,
                    ))
                else:
                    kind_value = part.get("kind")
                    kind = kind_value if isinstance(kind_value, str) and kind_value else "unknown"
                    payload = part.get("payload")
                    text = payload if kind in {"text", "reasoning"} and isinstance(payload, str) else None
                    decoded_payload = self._decoded_payload(payload)
                    mime_value = decoded_payload.get("mime") if decoded_payload else None
                    mime_type = mime_value if isinstance(mime_value, str) else None
                    invalid_payload = (
                        kind in {"text", "reasoning", "tool_call"}
                        and not isinstance(payload, str)
                    )
                    if kind in {"image", "file"}:
                        uri = decoded_payload.get("uri") if decoded_payload else None
                        name = decoded_payload.get("name") if decoded_payload else None
                        invalid_payload = not isinstance(uri, str) or not uri
                        if kind == "file":
                            invalid_payload = invalid_payload or not isinstance(name, str) or not name
                    if kind == "unknown":
                        warnings.append(ImportWarning(
                            code="invalid_message_part_kind",
                            message="Preserved a message part with no usable kind as an unknown block",
                            conversation_id=conversation_id,
                            node_id=node_id,
                        ))
                    elif invalid_payload:
                        warnings.append(ImportWarning(
                            code="invalid_message_part_payload",
                            message="Preserved a message part whose payload does not match its kind",
                            conversation_id=conversation_id,
                            node_id=node_id,
                        ))
                blocks.append(CanonicalContentBlock(
                    kind=kind,
                    text=text,
                    mime_type=mime_type,
                    raw=deepcopy(value),
                    provenance=self._block_provenance(
                        conversation_id, node_id, message_id, source_role, source_name, index
                    ),
                ))
            reasoning_text = raw.get("reasoningText")
            if isinstance(reasoning_text, str) and reasoning_text and not any(block.kind == "reasoning" for block in blocks):
                blocks.append(CanonicalContentBlock(
                    kind="reasoning",
                    text=reasoning_text,
                    raw={"legacy_field": "reasoningText", "value": reasoning_text},
                    provenance=self._block_provenance(
                        conversation_id, node_id, message_id, source_role, source_name, len(blocks)
                    ),
                ))
            return blocks
        content = raw.get("content")
        blocks: list[CanonicalContentBlock] = []
        if parts is not None:
            warnings.append(ImportWarning(
                code="invalid_message_parts",
                message="Preserved parts because the field is not a list",
                conversation_id=conversation_id,
                node_id=node_id,
            ))
            blocks.append(CanonicalContentBlock(
                kind="unknown",
                raw=deepcopy(parts),
                provenance=self._block_provenance(
                    conversation_id,
                    node_id,
                    message_id,
                    source_role,
                    source_name,
                    len(blocks),
                    {"source_field": "parts"},
                ),
            ))
        if isinstance(content, str):
            blocks.extend(self._legacy_content_blocks(
                content,
                conversation_id,
                node_id,
                message_id,
                source_role,
                source_name,
            ))
        elif content is not None:
            warnings.append(ImportWarning(
                code="invalid_message_content",
                message="Preserved non-string message content as an unknown block",
                conversation_id=conversation_id,
                node_id=node_id,
            ))
            blocks.append(CanonicalContentBlock(
                kind="unknown",
                raw=deepcopy(content),
                provenance=self._block_provenance(
                    conversation_id,
                    node_id,
                    message_id,
                    source_role,
                    source_name,
                    len(blocks),
                    {"source_field": "content"},
                ),
            ))
        return blocks

    @staticmethod
    def _decoded_payload(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, str):
            return None
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return as_mapping(decoded)

    def _legacy_content_blocks(
        self,
        content: str,
        conversation_id: str,
        node_id: str,
        message_id: str,
        source_role: str | None,
        source_name: str | None,
    ) -> list[CanonicalContentBlock]:
        if not content:
            return []
        blocks: list[CanonicalContentBlock] = []
        text_buffer: list[str] = []
        fence: tuple[str, int] | None = None

        def flush_text() -> None:
            if not text_buffer:
                return
            text = "".join(text_buffer)
            blocks.append(CanonicalContentBlock(
                kind="text",
                text=text,
                raw=text,
                provenance=self._block_provenance(
                    conversation_id, node_id, message_id, source_role, source_name, len(blocks)
                ),
            ))
            text_buffer.clear()

        for line_with_ending in content.splitlines(keepends=True):
            line = line_with_ending.rstrip("\r\n")
            if fence:
                text_buffer.append(line_with_ending)
                character, minimum = fence
                if re.fullmatch(rf" {{0,3}}{re.escape(character)}{{{minimum},}} *", line):
                    fence = None
                continue
            opening = _OPENING_FENCE.match(line)
            if opening:
                run = opening.group(2)
                fence = (run[0], len(run))
                text_buffer.append(line_with_ending)
                continue
            image = _IMAGE_MARKER.match(line)
            file_match = _FILE_MARKER.match(line)
            if image and image.group(1).strip():
                flush_text()
                uri = image.group(1).strip()
                blocks.append(CanonicalContentBlock(
                    kind="image",
                    raw={"legacy_marker": line, "uri": uri},
                    provenance=self._block_provenance(
                        conversation_id, node_id, message_id, source_role, source_name, len(blocks)
                    ),
                ))
            elif file_match and file_match.group(1).strip() and file_match.group(2).strip():
                flush_text()
                mime_type = file_match.group(3).strip() or None
                blocks.append(CanonicalContentBlock(
                    kind="file",
                    mime_type=mime_type,
                    raw={
                        "legacy_marker": line,
                        "uri": file_match.group(1).strip(),
                        "name": file_match.group(2).strip(),
                        "mime": mime_type,
                    },
                    provenance=self._block_provenance(
                        conversation_id, node_id, message_id, source_role, source_name, len(blocks)
                    ),
                ))
            else:
                text_buffer.append(line_with_ending)
        # splitlines(keepends=True) returns no final item for an empty suffix;
        # all original bytes are already present in the accumulated lines.
        flush_text()
        return blocks

    def _block_provenance(
        self,
        conversation_id: str,
        node_id: str,
        message_id: str,
        source_role: str | None,
        source_name: str | None,
        source_index: int,
        extra: dict[str, Any] | None = None,
    ) -> SourceProvenance:
        return SourceProvenance(
            platform=self.platform,
            format_id=self.format_id,
            source_name=source_name,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id,
            source_role=source_role,
            source_index=source_index,
            extra=extra or {},
        )
