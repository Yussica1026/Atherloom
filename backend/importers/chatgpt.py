from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from .archive import is_zip_payload
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
    ImportWarning,
    SourceProvenance,
)
from .registry import DetectionResult, ImporterError


class ChatGPTConversationsImporter:
    format_id = "chatgpt.conversations"
    platform = "chatgpt"
    aliases = ("openai", "chatgpt-conversations")
    file_extensions = (".json", ".zip")
    media_types = ("application/json", "application/zip")

    _numbered_zip_entry = re.compile(r"^conversations-\d{3}\.json$")

    _conversation_keys = {
        "id", "conversation_id", "title", "create_time", "update_time", "mapping", "current_node"
    }
    _node_keys = {"id", "message", "parent", "children"}
    _message_keys = {
        "id", "author", "create_time", "update_time", "content", "metadata", "status",
        "end_turn", "weight", "recipient", "channel",
    }

    def detect(self, payload: Any, source_name: str | None = None) -> DetectionResult | None:
        if is_zip_payload(payload):
            try:
                extracted = self._zip_conversations(payload)
            except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError, OSError):
                return None
            if extracted is None or self._detect_json(extracted[0]) is None:
                return None
            return DetectionResult(
                self.format_id,
                self.platform,
                0.999,
                ("zip", *extracted[1]["json_entries"], "conversation.mapping"),
            )
        return self._detect_json(payload)

    def _detect_json(self, payload: Any) -> DetectionResult | None:
        conversations = self._conversation_items(payload)
        samples = [item for item in conversations[:5] if isinstance(item, Mapping)]
        if any(isinstance(item.get("mapping"), (dict, list)) for item in samples):
            return DetectionResult(self.format_id, self.platform, 0.99, ("conversation.mapping",))
        if isinstance(payload, Mapping) and payload.get("mapping") is not None:
            return DetectionResult(self.format_id, self.platform, 0.99, ("root.mapping",))
        return None

    def parse(self, payload: Any, source_name: str | None = None) -> CanonicalImportBatch:
        container_metadata: dict[str, Any] | None = None
        if is_zip_payload(payload):
            try:
                extracted = self._zip_conversations(payload)
            except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
                raise ImporterError("ChatGPT ZIP contains no readable conversations.json") from exc
            if extracted is None:
                raise ImporterError(
                    "ChatGPT ZIP is missing root conversations.json or conversations-NNN.json"
                )
            payload, container_metadata = extracted
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
        provenance = SourceProvenance(
            platform=self.platform,
            format_id=self.format_id,
            source_name=source_name,
            extra=batch_extra,
        )
        return CanonicalImportBatch(
            format_id=self.format_id,
            platform=self.platform,
            conversations=conversations,
            warnings=warnings,
            provenance=provenance,
        )

    def _zip_conversations(
        self,
        payload: bytes,
    ) -> tuple[list[Any], dict[str, Any]] | None:
        with ZipFile(BytesIO(payload)) as archive:
            outer_entries = tuple(archive.namelist())
            direct_names = self._conversation_entry_names(outer_entries)
            decoded: list[tuple[str, Any]] = []
            nested_entries: dict[str, list[str]] = {}
            attachment_entries: list[str] = []
            if direct_names:
                decoded.extend(
                    (name, self._decode_zip_json(archive, name))
                    for name in direct_names
                )
            else:
                for outer_name in sorted(outer_entries):
                    basename = PurePosixPath(outer_name).name.lower()
                    if (
                        outer_name.endswith("/")
                        or not basename.endswith(".zip")
                    ):
                        continue
                    with archive.open(outer_name) as nested_file, ZipFile(nested_file) as inner:
                        inner_names = tuple(inner.namelist())
                        nested_entries[outer_name] = list(inner_names)
                        conversation_names = self._conversation_entry_names(inner_names)
                        for inner_name in conversation_names:
                            decoded.append((
                                f"{outer_name}!{inner_name}",
                                self._decode_zip_json(inner, inner_name),
                            ))
                        selected = set(conversation_names)
                        attachment_entries.extend(
                            f"{outer_name}!{inner_name}"
                            for inner_name in inner_names
                            if not inner_name.endswith("/") and inner_name not in selected
                        )
            if not decoded:
                return None

            selected_outer = {
                name.split("!", 1)[0]
                for name, _ in decoded
            }
            attachment_entries.extend(
                name for name in outer_entries
                if not name.endswith("/")
                and name not in direct_names
                and name not in selected_outer
            )

        ordered = sorted(decoded, key=lambda item: item[0])
        conversations: list[Any] = []
        for _, value in ordered:
            conversations.extend(self._conversation_items(value))
        json_entries = [name for name, _ in ordered]
        metadata: dict[str, Any] = {
            "type": "zip",
            "json_entries": json_entries,
            "entries": list(outer_entries),
            "other_entries": [
                name for name in outer_entries
                if name not in direct_names
            ],
            "attachment_entries": list(dict.fromkeys(attachment_entries)),
        }
        if nested_entries:
            metadata["nested_entries"] = nested_entries
        if len(ordered) == 1:
            metadata["json_entry"] = ordered[0][0]
        return conversations, metadata

    def _conversation_entry_names(self, names: tuple[str, ...]) -> list[str]:
        if "conversations.json" in names:
            return ["conversations.json"]
        return sorted(
            name for name in names
            if self._numbered_zip_entry.fullmatch(name) is not None
        )

    @staticmethod
    def _decode_zip_json(archive: ZipFile, entry_name: str) -> Any:
        return json.loads(archive.read(entry_name).decode("utf-8-sig"))

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
            raise ImporterError("ChatGPT conversations export must be an array or object")
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
        source_conversation_id = source_id(raw.get("id")) or source_id(raw.get("conversation_id"))
        conversation_id = source_conversation_id or f"chatgpt-conversation-{index + 1}"
        mapping = raw.get("mapping")
        entries: list[tuple[str | None, Any]] = []
        if isinstance(mapping, Mapping):
            entries = [(str(key), value) for key, value in mapping.items()]
        elif isinstance(mapping, list):
            entries = [(None, value) for value in mapping]
        elif mapping is not None:
            warnings.append(ImportWarning(
                code="invalid_mapping",
                message="Conversation mapping was not an object or array",
                conversation_id=conversation_id,
            ))

        nodes: list[CanonicalMessageNode] = []
        used_ids: set[str] = set()
        for order, (mapping_id, item) in enumerate(entries):
            node_raw = as_mapping(item)
            if node_raw is None:
                warnings.append(ImportWarning(
                    code="invalid_node", message=f"Skipped non-object mapping node at index {order}",
                    conversation_id=conversation_id,
                ))
                continue
            candidate = mapping_id or source_id(node_raw.get("id")) or f"node-{order + 1}"
            node_id = unique_id(candidate, used_ids)
            parent_id = source_id(node_raw.get("parent"))
            children_ids = [identifier for value in as_list(node_raw.get("children")) if (identifier := source_id(value))]
            node_provenance = SourceProvenance(
                platform=self.platform,
                format_id=self.format_id,
                source_name=source_name,
                conversation_id=source_conversation_id,
                node_id=source_id(node_raw.get("id")) or mapping_id,
                source_index=order,
                extra=unknown_fields(node_raw, self._node_keys),
            )
            message = self._parse_message(
                node_raw.get("message"), conversation_id, source_conversation_id, node_id,
                source_name, order, warnings,
            )
            nodes.append(CanonicalMessageNode(
                id=node_id,
                parent_id=parent_id,
                children_ids=list(dict.fromkeys(children_ids)),
                message=message,
                source_order=order,
                provenance=node_provenance,
            ))

        current_node_id = source_id(raw.get("current_node"))
        known_node_ids = {node.id for node in nodes}
        if current_node_id not in known_node_ids:
            current_node_id = None
        roots, branches = finish_graph(nodes, current_node_id)
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        provenance = SourceProvenance(
            platform=self.platform,
            format_id=self.format_id,
            source_name=source_name,
            conversation_id=source_conversation_id,
            source_index=index,
            extra={
                **unknown_fields(raw, self._conversation_keys),
                "raw_create_time": deepcopy(raw.get("create_time")),
                "raw_update_time": deepcopy(raw.get("update_time")),
            },
        )
        return CanonicalConversation(
            id=conversation_id,
            title=title,
            created_at=normalize_timestamp(raw.get("create_time")),
            updated_at=normalize_timestamp(raw.get("update_time")),
            current_node_id=current_node_id,
            root_node_ids=roots,
            nodes=nodes,
            branches=branches,
            metadata={},
            provenance=provenance,
        )

    def _parse_message(
        self,
        value: Any,
        canonical_conversation_id: str,
        source_conversation_id: str | None,
        node_id: str,
        source_name: str | None,
        order: int,
        warnings: list[ImportWarning],
    ) -> CanonicalMessage | None:
        if value is None:
            return None
        raw = as_mapping(value)
        if raw is None:
            warnings.append(ImportWarning(
                code="invalid_message", message="Node message was not an object",
                conversation_id=canonical_conversation_id, node_id=node_id,
            ))
            return None
        author = as_mapping(raw.get("author")) or {}
        source_role = author.get("role") if isinstance(author.get("role"), str) else None
        message_id = source_id(raw.get("id")) or f"{node_id}:message"
        content, content_metadata = self._parse_content(
            raw.get("content"), source_conversation_id, node_id, message_id, source_role, source_name
        )
        source_metadata = as_mapping(raw.get("metadata")) or {}
        content.extend(self._metadata_attachment_blocks(
            source_metadata.get("attachments"),
            source_conversation_id,
            node_id,
            message_id,
            source_role,
            source_name,
            len(content),
        ))
        metadata = {
            **deepcopy(source_metadata),
            "source_status": deepcopy(raw.get("status")),
            "source_end_turn": deepcopy(raw.get("end_turn")),
            "source_recipient": deepcopy(raw.get("recipient")),
            "source_channel": deepcopy(raw.get("channel")),
            "content_metadata": content_metadata,
        }
        provenance = SourceProvenance(
            platform=self.platform,
            format_id=self.format_id,
            source_name=source_name,
            conversation_id=source_conversation_id,
            node_id=node_id,
            message_id=source_id(raw.get("id")),
            source_role=source_role,
            source_index=order,
            extra={
                **unknown_fields(raw, self._message_keys),
                "author": deepcopy(author),
                "raw_create_time": deepcopy(raw.get("create_time")),
                "raw_update_time": deepcopy(raw.get("update_time")),
            },
        )
        return CanonicalMessage(
            id=message_id,
            role=normalize_role(source_role, {
                "user": "user", "assistant": "assistant", "system": "system",
                "developer": "system", "tool": "tool",
            }),
            created_at=normalize_timestamp(raw.get("create_time")),
            updated_at=normalize_timestamp(raw.get("update_time")),
            content=content,
            metadata=metadata,
            provenance=provenance,
        )

    def _metadata_attachment_blocks(
        self,
        value: Any,
        conversation_id: str | None,
        node_id: str,
        message_id: str,
        source_role: str | None,
        source_name: str | None,
        start_index: int,
    ) -> list[CanonicalContentBlock]:
        blocks: list[CanonicalContentBlock] = []
        for attachment_index, value in enumerate(as_list(value)):
            attachment = as_mapping(value)
            mime = None
            if attachment is not None:
                mime_value = attachment.get("mime_type") or attachment.get("mimeType")
                mime = mime_value if isinstance(mime_value, str) else None
            blocks.append(CanonicalContentBlock(
                kind="attachment",
                mime_type=mime,
                raw=deepcopy(value),
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=conversation_id,
                    node_id=node_id,
                    message_id=message_id,
                    source_role=source_role,
                    source_index=start_index + attachment_index,
                    extra={"metadata_attachment_index": attachment_index},
                ),
            ))
        return blocks

    def _parse_content(
        self,
        value: Any,
        conversation_id: str | None,
        node_id: str,
        message_id: str,
        source_role: str | None,
        source_name: str | None,
    ) -> tuple[list[CanonicalContentBlock], dict[str, Any]]:
        wrapper = as_mapping(value)
        if wrapper is not None:
            content_type = wrapper.get("content_type") if isinstance(wrapper.get("content_type"), str) else "unknown"
            if isinstance(wrapper.get("parts"), list):
                values = wrapper["parts"]
                wrapper_extra = unknown_fields(wrapper, {"parts"})
            else:
                values = [wrapper]
                wrapper_extra = {}
        elif value is None:
            return [], {}
        else:
            content_type = "text" if isinstance(value, str) else "unknown"
            values = value if isinstance(value, list) else [value]
            wrapper_extra = {}

        blocks: list[CanonicalContentBlock] = []
        for block_index, part in enumerate(values):
            part_mapping = as_mapping(part)
            if isinstance(part, str):
                kind, text, mime_type = ("text" if content_type == "text" else content_type), part, None
            elif part_mapping is not None:
                kind_value = part_mapping.get("content_type") or part_mapping.get("type") or content_type
                kind = kind_value if isinstance(kind_value, str) and kind_value else "unknown"
                text_value = part_mapping.get("text")
                if not isinstance(text_value, str) and kind == "text" and isinstance(part_mapping.get("content"), str):
                    text_value = part_mapping["content"]
                text = text_value if isinstance(text_value, str) else None
                mime = part_mapping.get("mime_type") or part_mapping.get("mimeType")
                mime_type = mime if isinstance(mime, str) else None
            else:
                kind, text, mime_type = "unknown", None, None
            blocks.append(CanonicalContentBlock(
                kind=kind,
                text=text,
                mime_type=mime_type,
                raw=deepcopy(part),
                provenance=SourceProvenance(
                    platform=self.platform,
                    format_id=self.format_id,
                    source_name=source_name,
                    conversation_id=conversation_id,
                    node_id=node_id,
                    message_id=message_id,
                    source_role=source_role,
                    source_index=block_index,
                    extra={"wrapper_content_type": content_type},
                ),
            ))
        return blocks, wrapper_extra
