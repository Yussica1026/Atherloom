from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from .archive import ZipEntryPayload, is_zip_payload
from .common import (
    as_list,
    as_mapping,
    finish_graph,
    first_present,
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


class ClaudeConversationsImporter:
    format_id = "claude.conversations"
    platform = "claude"
    aliases = ("anthropic", "claude-conversations")
    file_extensions = (".json", ".zip")
    media_types = ("application/json", "application/zip")

    _conversation_keys = {
        "uuid", "id", "name", "title", "summary", "created_at", "updated_at", "chat_messages",
        "current_node", "current_node_id", "current_message_uuid", "current_leaf_message_uuid",
    }
    _message_keys = {
        "uuid", "id", "sender", "role", "created_at", "updated_at", "text", "content", "metadata",
        "attachments", "files", "files_v2", "parent_message_uuid", "parent_uuid", "parent_message_id", "parent",
        "children", "branch_uuid", "branch_id",
    }
    _parent_keys = ("parent_message_uuid", "parent_uuid", "parent_message_id", "parent")
    _branch_keys = ("branch_uuid", "branch_id")

    def detect(self, payload: Any, source_name: str | None = None) -> DetectionResult | None:
        if is_zip_payload(payload):
            try:
                extracted = self._zip_conversations(payload)
            except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError, OSError):
                return None
            if extracted is None or self._detect_json(extracted.value) is None:
                return None
            return DetectionResult(
                self.format_id,
                self.platform,
                0.999,
                ("zip", extracted.entry_name, "conversation.chat_messages"),
            )
        return self._detect_json(payload)

    def _detect_json(self, payload: Any) -> DetectionResult | None:
        conversations = self._conversation_items(payload)
        samples = [item for item in conversations[:5] if isinstance(item, Mapping)]
        if any(isinstance(item.get("chat_messages"), list) for item in samples):
            return DetectionResult(self.format_id, self.platform, 0.99, ("conversation.chat_messages",))
        if isinstance(payload, Mapping) and isinstance(payload.get("chat_messages"), list):
            return DetectionResult(self.format_id, self.platform, 0.99, ("root.chat_messages",))
        return None

    def parse(self, payload: Any, source_name: str | None = None) -> CanonicalImportBatch:
        container_metadata: dict[str, Any] | None = None
        if is_zip_payload(payload):
            try:
                extracted = self._zip_conversations(payload)
            except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
                raise ImporterError("Claude ZIP contains no readable conversations.json") from exc
            if extracted is None:
                raise ImporterError("Claude ZIP is missing conversations.json")
            payload = extracted.value
            container_metadata = extracted.provenance()
        conversations_raw, batch_extra = self._unwrap(payload)
        if container_metadata is not None:
            batch_extra["container"] = container_metadata
        warnings: list[ImportWarning] = []
        conversations: list[CanonicalConversation] = []
        for index, raw in enumerate(conversations_raw):
            conversation = as_mapping(raw)
            if conversation is None:
                warnings.append(ImportWarning(code="invalid_conversation", message=f"Skipped non-object item at index {index}"))
                continue
            conversations.append(self._parse_conversation(conversation, index, source_name, warnings))
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
    def _zip_conversations(payload: bytes) -> ZipEntryPayload | None:
        """Read Claude's account-folder or legacy root conversations file."""

        with ZipFile(BytesIO(payload)) as archive:
            entries = tuple(archive.namelist())
            candidates = sorted(
                (
                    name for name in entries
                    if not name.endswith("/")
                    and PurePosixPath(name).name.lower() == "conversations.json"
                ),
                key=lambda name: (len(PurePosixPath(name).parts), name.lower()),
            )
            if not candidates:
                return None
            entry_name = candidates[0]
            value = json.loads(archive.read(entry_name).decode("utf-8-sig"))
            return ZipEntryPayload(entry_name, value, entries)

    @staticmethod
    def _conversation_items(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        mapping = as_mapping(payload)
        if mapping is None:
            return []
        if isinstance(mapping.get("conversations"), list):
            return mapping["conversations"]
        return [mapping]

    def _unwrap(self, payload: Any) -> tuple[list[Any], dict[str, Any]]:
        if isinstance(payload, list):
            return payload, {}
        mapping = as_mapping(payload)
        if mapping is None:
            raise ImporterError("Claude conversations export must be an array or object")
        if isinstance(mapping.get("conversations"), list):
            return mapping["conversations"], unknown_fields(mapping, {"conversations"})
        return [mapping], {}

    def _parse_conversation(
        self,
        raw: dict[str, Any],
        index: int,
        source_name: str | None,
        warnings: list[ImportWarning],
    ) -> CanonicalConversation:
        source_conversation_id = source_id(raw.get("uuid")) or source_id(raw.get("id"))
        conversation_id = source_conversation_id or f"claude-conversation-{index + 1}"
        message_values = as_list(raw.get("chat_messages"))
        nodes: list[CanonicalMessageNode] = []
        used_ids: set[str] = set()
        previous_id: str | None = None
        last_by_branch: dict[str, str] = {}

        for order, value in enumerate(message_values):
            message_raw = as_mapping(value)
            if message_raw is None:
                warnings.append(ImportWarning(
                    code="invalid_message", message=f"Skipped non-object chat message at index {order}",
                    conversation_id=conversation_id,
                ))
                continue
            source_message_id = source_id(message_raw.get("uuid")) or source_id(message_raw.get("id"))
            node_id = unique_id(source_message_id or f"message-{order + 1}", used_ids)
            branch_present, branch_value = first_present(message_raw, self._branch_keys)
            branch_id = source_id(branch_value) if branch_present else None
            parent_present, parent_value = first_present(message_raw, self._parent_keys)
            if parent_present:
                parent_id = source_id(parent_value)
                if parent_id in {
                    "00000000-0000-0000-0000-000000000000",
                    "00000000-0000-4000-8000-000000000000",
                }:
                    parent_id = None
                parent_inferred = False
            else:
                parent_id = last_by_branch.get(branch_id) if branch_id else previous_id
                parent_inferred = parent_id is not None
            children_ids = [identifier for child in as_list(message_raw.get("children")) if (identifier := source_id(child))]
            message = self._parse_message(
                message_raw, conversation_id, source_conversation_id, node_id, branch_id,
                source_name, order,
            )
            nodes.append(CanonicalMessageNode(
                id=node_id,
                parent_id=parent_id,
                children_ids=list(dict.fromkeys(children_ids)),
                message=message,
                source_order=order,
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=source_conversation_id,
                    node_id=source_message_id,
                    message_id=source_message_id,
                    branch_id=branch_id,
                    source_index=order,
                    extra={
                        "parent_inferred": parent_inferred,
                        "raw_parent": deepcopy(parent_value) if parent_present else None,
                    },
                ),
            ))
            previous_id = node_id
            if branch_id:
                last_by_branch[branch_id] = node_id

        current_node_id = None
        for key in ("current_node", "current_node_id", "current_message_uuid", "current_leaf_message_uuid"):
            current_node_id = source_id(raw.get(key))
            if current_node_id:
                break
        known_node_ids = {node.id for node in nodes}
        if current_node_id not in known_node_ids:
            current_node_id = None
        roots, branches = finish_graph(nodes, current_node_id)
        if current_node_id is None and len(branches) == 1:
            current_node_id = branches[0].leaf_node_id
            branches[0].is_current = True
        title_value = raw.get("name") if isinstance(raw.get("name"), str) else raw.get("title")
        title = title_value if isinstance(title_value, str) else ""
        summary = raw.get("summary")
        metadata = {"summary": deepcopy(summary)} if summary is not None else {}
        account = as_mapping(raw.get("account"))
        account_id = (
            source_id(account.get("uuid")) or source_id(account.get("id"))
            if account is not None
            else None
        )
        participants = []
        if account_id:
            participants.append(CanonicalParticipant(
                id=account_id,
                kind="account",
                metadata=deepcopy(account) if account is not None else {},
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=source_conversation_id,
                    extra={"source_field": "account.uuid"},
                ),
            ))
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
            metadata=metadata,
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=source_conversation_id,
                source_index=index,
                extra={
                    **unknown_fields(raw, self._conversation_keys),
                    "raw_created_at": deepcopy(raw.get("created_at")),
                    "raw_updated_at": deepcopy(raw.get("updated_at")),
                },
            ),
        )

    def _parse_message(
        self,
        raw: dict[str, Any],
        canonical_conversation_id: str,
        source_conversation_id: str | None,
        node_id: str,
        branch_id: str | None,
        source_name: str | None,
        order: int,
    ) -> CanonicalMessage:
        source_message_id = source_id(raw.get("uuid")) or source_id(raw.get("id"))
        source_role_value = raw.get("sender") if isinstance(raw.get("sender"), str) else raw.get("role")
        source_role = source_role_value if isinstance(source_role_value, str) else None
        content = self._parse_content(
            raw, source_conversation_id, node_id, source_message_id or node_id,
            branch_id, source_role, source_name,
        )
        source_metadata = as_mapping(raw.get("metadata")) or {}
        provenance_extra = unknown_fields(raw, self._message_keys)
        provenance_extra.update({
            "raw_text": deepcopy(raw.get("text")),
            "raw_sender": deepcopy(raw.get("sender")),
            "raw_role": deepcopy(raw.get("role")),
            "raw_created_at": deepcopy(raw.get("created_at")),
            "raw_updated_at": deepcopy(raw.get("updated_at")),
        })
        return CanonicalMessage(
            id=source_message_id or f"{node_id}:message",
            role=normalize_role(source_role, {
                "human": "user", "user": "user", "assistant": "assistant",
                "system": "system", "tool": "tool",
            }),
            created_at=normalize_timestamp(raw.get("created_at")),
            updated_at=normalize_timestamp(raw.get("updated_at")),
            content=content,
            metadata=deepcopy(source_metadata),
            provenance=SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=source_conversation_id,
                node_id=node_id,
                message_id=source_message_id,
                branch_id=branch_id,
                source_role=source_role,
                source_index=order,
                extra=provenance_extra,
            ),
        )

    def _parse_content(
        self,
        message: dict[str, Any],
        conversation_id: str | None,
        node_id: str,
        message_id: str,
        branch_id: str | None,
        source_role: str | None,
        source_name: str | None,
    ) -> list[CanonicalContentBlock]:
        content_value = message.get("content")
        content_values = as_list(content_value)
        if not content_values and content_value is not None and not isinstance(content_value, (list, tuple)):
            content_values = [content_value]

        values: list[tuple[Any, str | None, str, int]] = [
            (value, None, "content", index)
            for index, value in enumerate(content_values)
        ]
        message_text = message.get("text")
        has_text_block = any(
            isinstance(value, str)
            or (
                (mapping := as_mapping(value)) is not None
                and (mapping.get("type") or mapping.get("content_type")) == "text"
            )
            for value in content_values
        )
        if isinstance(message_text, str) and message_text and not has_text_block:
            values.append((message_text, None, "text", 0))
        values.extend(
            (value, "attachment", "attachments", index)
            for index, value in enumerate(as_list(message.get("attachments")))
        )
        file_values_v2 = as_list(message.get("files_v2"))
        file_values = file_values_v2 or as_list(message.get("files"))
        file_source_field = "files_v2" if file_values_v2 else "files"
        values.extend(
            (value, "file", file_source_field, index)
            for index, value in enumerate(file_values)
        )

        blocks: list[CanonicalContentBlock] = []
        for block_index, (value, forced_kind, source_field, source_field_index) in enumerate(values):
            block = as_mapping(value)
            if isinstance(value, str):
                kind = forced_kind or "text"
                text = value if forced_kind is None else None
                mime_type = None
            elif block is not None:
                type_value = block.get("type") or block.get("content_type")
                kind = forced_kind or (
                    type_value if isinstance(type_value, str) and type_value else "unknown"
                )
                text_value = block.get("text")
                if not isinstance(text_value, str) and kind == "thinking":
                    text_value = block.get("thinking")
                if not isinstance(text_value, str) and kind == "attachment":
                    text_value = block.get("extracted_content")
                if not isinstance(text_value, str) and kind in {"text", "tool_result"}:
                    text_value = self._nested_text(block.get("content"))
                text = text_value if isinstance(text_value, str) else None
                mime_value = block.get("mime_type") or block.get("file_type")
                mime_type = mime_value if isinstance(mime_value, str) else None
            else:
                kind, text, mime_type = "unknown", None, None
            blocks.append(CanonicalContentBlock(
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
                    branch_id=branch_id,
                    source_role=source_role,
                    source_index=block_index,
                    extra={
                        "source_field": source_field,
                        "source_field_index": source_field_index,
                    },
                ),
            ))
        return blocks

    @staticmethod
    def _nested_text(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return None
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            mapping = as_mapping(item)
            if mapping is not None and isinstance(mapping.get("text"), str):
                parts.append(mapping["text"])
        return "\n".join(parts) if parts else None
