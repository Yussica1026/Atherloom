from __future__ import annotations

import json
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


class AstrBotConversationsImporter:
    """Parse AstrBot's official JSONL export and conversation API records."""

    format_id = "astrbot.conversations_jsonl"
    platform = "astrbot"
    aliases = ("astrbot-jsonl", "astrbot-conversations")
    file_extensions = (".jsonl",)
    media_types = ("application/jsonl", "application/x-ndjson")

    _conversation_keys = {
        "cid",
        "user_id",
        "platform_id",
        "title",
        "persona_id",
        "created_at",
        "updated_at",
        "content",
        "history",
    }
    _message_keys = {
        "id",
        "message_id",
        "role",
        "content",
        "tool_calls",
        "tool_call_id",
    }

    def detect(self, payload: Any, source_name: str | None = None) -> DetectionResult | None:
        samples = self._detection_samples(payload)
        for sample in samples:
            raw = as_mapping(sample)
            if raw is None or not self._matches_official_record(raw):
                continue
            history_field = "content" if "content" in raw else "history"
            reasons = [
                "conversation.cid",
                "conversation.user_id",
                f"conversation.{history_field}",
            ]
            if source_id(raw.get("platform_id")):
                reasons.append("conversation.platform_id")
            if isinstance(source_name, str) and source_name.lower().endswith(".jsonl"):
                reasons.append("source_name.jsonl")
            return DetectionResult(
                self.format_id,
                self.platform,
                0.99 if history_field == "content" else 0.97,
                tuple(reasons),
            )
        return None

    def parse(self, payload: Any, source_name: str | None = None) -> CanonicalImportBatch:
        warnings: list[ImportWarning] = []
        records = self._decode_records(payload, warnings)
        conversations: list[CanonicalConversation] = []

        for source_index, location, value in records:
            raw = as_mapping(value)
            if raw is None or not self._has_conversation_fields(raw):
                warnings.append(
                    ImportWarning(
                        code="invalid_conversation",
                        message=f"Skipped invalid AstrBot conversation record at {location}",
                    )
                )
                continue
            try:
                conversations.append(
                    self._parse_conversation(
                        raw,
                        source_index,
                        location,
                        source_name,
                        warnings,
                    )
                )
            except (OverflowError, TypeError, ValueError):
                # A bad decoded record is source data, not a reason to lose
                # otherwise valid lines.  Keep warnings free of raw values so
                # previews cannot echo private conversation content.
                warnings.append(
                    ImportWarning(
                        code="invalid_conversation",
                        message=f"Skipped unreadable AstrBot conversation at {location}",
                        conversation_id=source_id(raw.get("cid")),
                    )
                )

        return CanonicalImportBatch(
            format_id=self.format_id,
            platform=self.platform,
            conversations=conversations,
            warnings=warnings,
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
            ),
        )

    def _detection_samples(self, payload: Any) -> list[Any]:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8-sig")
            except UnicodeDecodeError:
                return []
        if isinstance(payload, str):
            samples: list[Any] = []
            for line in payload.lstrip("\ufeff").splitlines():
                if not line.strip():
                    continue
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError:
                    continue
                samples.extend(self._records_from_value(decoded))
                if len(samples) >= 20:
                    break
            return samples
        return self._records_from_value(payload)[:20]

    @classmethod
    def _records_from_value(cls, value: Any) -> list[Any]:
        """Unwrap only AstrBot's published service/API containers."""

        if isinstance(value, list):
            return list(value)
        raw = as_mapping(value)
        if raw is None:
            return [value]

        conversations = raw.get("conversations")
        if isinstance(conversations, list):
            return list(conversations)

        data = as_mapping(raw.get("data"))
        if data is not None:
            conversations = data.get("conversations")
            if isinstance(conversations, list):
                return list(conversations)
            if cls._has_conversation_fields(data):
                return [data]
        return [raw]

    @staticmethod
    def _has_conversation_fields(raw: Mapping[str, Any]) -> bool:
        return bool(
            {"cid", "user_id", "platform_id"}.intersection(raw)
            and {"content", "history"}.intersection(raw)
        )

    @classmethod
    def _matches_official_record(cls, raw: Mapping[str, Any]) -> bool:
        user_id = source_id(raw.get("user_id"))
        has_platform_identity = source_id(raw.get("platform_id")) is not None
        if user_id:
            parts = user_id.split(":", 2)
            has_platform_identity = has_platform_identity or (
                len(parts) == 3 and all(parts)
            )
        return bool(
            source_id(raw.get("cid"))
            and user_id
            and has_platform_identity
            and {"content", "history"}.intersection(raw)
        )

    def _decode_records(
        self,
        payload: Any,
        warnings: list[ImportWarning],
    ) -> list[tuple[int, str, Any]]:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ImporterError("AstrBot export is not valid UTF-8 JSONL") from exc

        if isinstance(payload, str):
            records: list[tuple[int, str, Any]] = []
            for line_number, line in enumerate(payload.lstrip("\ufeff").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError as exc:
                    warnings.append(
                        ImportWarning(
                            code="invalid_jsonl_line",
                            message=f"Skipped invalid JSON at line {line_number}: {exc.msg}",
                        )
                    )
                    continue
                values = self._records_from_value(decoded)
                for nested_index, value in enumerate(values):
                    location = f"line {line_number}"
                    if len(values) > 1:
                        location += f" item {nested_index}"
                    records.append((len(records), location, value))
            return records

        if isinstance(payload, (list, Mapping)):
            values = self._records_from_value(payload)
            if isinstance(payload, list):
                prefix = "index"
            elif len(values) == 1 and values[0] is payload:
                prefix = "root"
            else:
                prefix = "conversation"
            return [
                (index, prefix if prefix == "root" else f"{prefix} {index}", value)
                for index, value in enumerate(values)
            ]
        raise ImporterError(
            "AstrBot conversations export must be JSONL text, a decoded object, or an array"
        )

    def _parse_conversation(
        self,
        raw: dict[str, Any],
        source_index: int,
        location: str,
        source_name: str | None,
        warnings: list[ImportWarning],
    ) -> CanonicalConversation:
        source_conversation_id = source_id(raw.get("cid"))
        conversation_id = source_conversation_id or f"astrbot-conversation-{source_index + 1}"
        if source_conversation_id is None:
            warnings.append(
                ImportWarning(
                    code="missing_conversation_id",
                    message=f"Conversation at {location} had no cid; a deterministic ID was assigned",
                    conversation_id=conversation_id,
                )
            )

        source_user_id = source_id(raw.get("user_id"))
        umo_metadata = self._parse_umo(source_user_id) if source_user_id else {}
        source_platform_id = source_id(raw.get("platform_id")) or source_id(
            umo_metadata.get("platform_id")
        )
        source_persona_id = source_id(raw.get("persona_id"))
        if source_user_id is None:
            warnings.append(
                ImportWarning(
                    code="missing_user_id",
                    message=f"Conversation at {location} had no AstrBot user_id/UMO",
                    conversation_id=conversation_id,
                )
            )
        if source_platform_id is None:
            warnings.append(
                ImportWarning(
                    code="missing_platform_id",
                    message=f"Conversation at {location} had no platform_id",
                    conversation_id=conversation_id,
                )
            )

        history_field = "content" if "content" in raw else "history"
        message_values = self._decode_history(
            raw.get(history_field),
            history_field,
            location,
            conversation_id,
            warnings,
        )
        participants: list[CanonicalParticipant] = []
        session_participant: CanonicalParticipant | None = None
        persona_participant: CanonicalParticipant | None = None
        if source_user_id:
            session_participant = CanonicalParticipant(
                id=source_user_id,
                kind="source_session",
                platform_id=source_platform_id,
                metadata={
                    **deepcopy(umo_metadata),
                    "participants_complete": False,
                },
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=source_conversation_id,
                    extra={"source_field": "user_id", "is_sender_identity": False},
                ),
            )
            participants.append(session_participant)
        if source_persona_id:
            persona_participant = CanonicalParticipant(
                id=source_persona_id,
                kind="persona",
                platform_id=source_platform_id,
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=source_conversation_id,
                    extra={"source_field": "persona_id"},
                ),
            )
            participants.append(persona_participant)

        nodes: list[CanonicalMessageNode] = []
        used_ids: set[str] = set()
        previous_id: str | None = None
        for order, value in enumerate(message_values):
            message_raw = as_mapping(value)
            if message_raw is None:
                warnings.append(
                    ImportWarning(
                        code="invalid_message",
                        message=f"Skipped non-object content item at index {order}",
                        conversation_id=conversation_id,
                    )
                )
                continue

            source_message_id = source_id(message_raw.get("id")) or source_id(
                message_raw.get("message_id")
            )
            node_id = unique_id(source_message_id or f"message-{order + 1}", used_ids)
            message = self._parse_message(
                message_raw,
                source_conversation_id,
                node_id,
                source_message_id or f"{node_id}:message",
                source_name,
                order,
                persona_participant,
            )
            nodes.append(
                CanonicalMessageNode(
                    id=node_id,
                    parent_id=previous_id,
                    children_ids=[],
                    message=message,
                    source_order=order,
                    provenance=SourceProvenance(
                        platform=self.platform,
                        format_id=self.format_id,
                        source_name=source_name,
                        conversation_id=source_conversation_id,
                        node_id=source_message_id,
                        message_id=source_message_id,
                        source_index=order,
                        extra={"parent_inferred": previous_id is not None},
                    ),
                )
            )
            previous_id = node_id

        current_node_id = nodes[-1].id if nodes else None
        roots, branches = finish_graph(nodes, current_node_id)
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        identity_metadata: dict[str, Any] = {
            "source_platform_id": source_platform_id,
            "source_user_id": deepcopy(raw.get("user_id")),
            "source_persona_id": deepcopy(raw.get("persona_id")),
            "source_history_field": history_field,
            "source_context": {
                "conversation_id": deepcopy(raw.get("cid")),
                "platform_id": source_platform_id,
                "user_id": deepcopy(raw.get("user_id")),
                "persona_id": deepcopy(raw.get("persona_id")),
                "umo": deepcopy(umo_metadata),
                "scope": umo_metadata.get("scope"),
                # The official export has no group member list or per-message
                # sender IDs.  Keep that absence explicit instead of treating
                # the UMO session segment as a person or group participant.
                "participants_complete": False,
            },
        }
        if source_user_id:
            identity_metadata["unified_message_origin"] = umo_metadata

        return CanonicalConversation(
            id=conversation_id,
            title=title,
            created_at=normalize_timestamp(raw.get("created_at")),
            updated_at=normalize_timestamp(raw.get("updated_at")),
            current_node_id=current_node_id,
            root_node_ids=roots,
            nodes=nodes,
            branches=branches,
            participants=participants,
            metadata=identity_metadata,
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=source_conversation_id,
                source_index=source_index,
                extra={
                    **unknown_fields(raw, self._conversation_keys),
                    "raw_user_id": deepcopy(raw.get("user_id")),
                    "raw_platform_id": deepcopy(raw.get("platform_id")),
                    "raw_persona_id": deepcopy(raw.get("persona_id")),
                    "raw_created_at": deepcopy(raw.get("created_at")),
                    "raw_updated_at": deepcopy(raw.get("updated_at")),
                    "raw_content": deepcopy(raw.get("content")),
                    "raw_history": deepcopy(raw.get("history")),
                },
            ),
        )

    @staticmethod
    def _decode_history(
        value: Any,
        field_name: str,
        location: str,
        conversation_id: str,
        warnings: list[ImportWarning],
    ) -> list[Any]:
        decoded = value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                warnings.append(
                    ImportWarning(
                        code="invalid_history_json",
                        message=f"Conversation at {location} had invalid JSON in {field_name}",
                        conversation_id=conversation_id,
                    )
                )
                return []
        if isinstance(decoded, list):
            return list(decoded)
        warnings.append(
            ImportWarning(
                code="invalid_content",
                message=f"Conversation at {location} had non-array {field_name}",
                conversation_id=conversation_id,
            )
        )
        return []

    def _parse_message(
        self,
        raw: dict[str, Any],
        conversation_id: str | None,
        node_id: str,
        message_id: str,
        source_name: str | None,
        order: int,
        persona_participant: CanonicalParticipant | None,
    ) -> CanonicalMessage:
        source_role = raw.get("role") if isinstance(raw.get("role"), str) else None
        blocks = self._parse_content(
            raw.get("content"),
            conversation_id,
            node_id,
            message_id,
            source_role,
            source_name,
        )
        for tool_call in as_list(raw.get("tool_calls")):
            blocks.append(
                self._content_block(
                    kind="tool_call",
                    value=tool_call,
                    text=None,
                    conversation_id=conversation_id,
                    node_id=node_id,
                    message_id=message_id,
                    source_role=source_role,
                    source_name=source_name,
                    source_index=len(blocks),
                )
            )

        metadata: dict[str, Any] = {}
        if "tool_calls" in raw:
            metadata["source_tool_calls"] = deepcopy(raw.get("tool_calls"))
        if "tool_call_id" in raw:
            metadata["source_tool_call_id"] = deepcopy(raw.get("tool_call_id"))

        role = normalize_role(
            source_role,
            {
                "user": "user",
                "assistant": "assistant",
                "system": "system",
                "tool": "tool",
            },
        )
        return CanonicalMessage(
            id=message_id,
            role=role,
            content=blocks,
            author=persona_participant if role == "assistant" else None,
            metadata=metadata,
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=conversation_id,
                node_id=node_id,
                message_id=source_id(raw.get("id")) or source_id(raw.get("message_id")),
                source_role=source_role,
                source_index=order,
                extra={
                    **unknown_fields(raw, self._message_keys),
                    "raw_role": deepcopy(raw.get("role")),
                    "raw_content": deepcopy(raw.get("content")),
                    "raw_tool_calls": deepcopy(raw.get("tool_calls")),
                    "raw_tool_call_id": deepcopy(raw.get("tool_call_id")),
                },
            ),
        )

    def _parse_content(
        self,
        value: Any,
        conversation_id: str | None,
        node_id: str,
        message_id: str,
        source_role: str | None,
        source_name: str | None,
    ) -> list[CanonicalContentBlock]:
        values = as_list(value)
        if not values and value is not None and not isinstance(value, (list, tuple)):
            values = [value]

        blocks: list[CanonicalContentBlock] = []
        for block_index, item in enumerate(values):
            mapping = as_mapping(item)
            if isinstance(item, str):
                kind, text, mime_type = "text", item, None
            elif mapping is not None:
                type_value = mapping.get("type") or mapping.get("content_type")
                if isinstance(type_value, str) and type_value:
                    kind = type_value
                elif source_role == "_checkpoint":
                    kind = "_checkpoint"
                else:
                    kind = "unknown"
                text_value = mapping.get("text")
                if not isinstance(text_value, str) and kind == "think":
                    text_value = mapping.get("think")
                if not isinstance(text_value, str) and kind == "text":
                    text_value = mapping.get("content")
                text = text_value if isinstance(text_value, str) else None
                mime_value = mapping.get("mime_type") or mapping.get("mimeType")
                mime_type = mime_value if isinstance(mime_value, str) else None
            else:
                kind, text, mime_type = "unknown", None, None

            blocks.append(
                self._content_block(
                    kind=kind,
                    value=item,
                    text=text,
                    mime_type=mime_type,
                    conversation_id=conversation_id,
                    node_id=node_id,
                    message_id=message_id,
                    source_role=source_role,
                    source_name=source_name,
                    source_index=block_index,
                )
            )
        return blocks

    def _content_block(
        self,
        *,
        kind: str,
        value: Any,
        text: str | None,
        conversation_id: str | None,
        node_id: str,
        message_id: str,
        source_role: str | None,
        source_name: str | None,
        source_index: int,
        mime_type: str | None = None,
    ) -> CanonicalContentBlock:
        return CanonicalContentBlock(
            kind=kind,
            text=text,
            mime_type=mime_type,
            raw=deepcopy(value),
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=conversation_id,
                node_id=node_id,
                message_id=message_id,
                source_role=source_role,
                source_index=source_index,
            ),
        )

    @staticmethod
    def _parse_umo(value: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {"value": value}
        parts = value.split(":", 2)
        if len(parts) != 3:
            return metadata
        platform_id, message_type, session_id = parts
        metadata.update(
            {
                "platform_id": platform_id,
                "message_type": message_type,
                "session_id": session_id,
            }
        )
        normalized_type = message_type.lower()
        if "group" in normalized_type:
            metadata["scope"] = "group"
        elif "friend" in normalized_type or "private" in normalized_type:
            metadata["scope"] = "private"
        return metadata
