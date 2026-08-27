from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.importers.api_models import ImportConversationPlan, ImportMessagePlan
from backend.importers.models import (
    CanonicalContentBlock,
    CanonicalConversation,
    CanonicalImportBatch,
    CanonicalMessage,
    CanonicalMessageNode,
)
from backend.importers.registry import ImporterRegistry
from backend.importers.repository import ImportBatchConflict, ImportBatchRepository


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _attachment_name(block: CanonicalContentBlock) -> str | None:
    raw = block.raw
    if not isinstance(raw, dict):
        return None
    candidates = [raw]
    value = raw.get("value")
    if isinstance(value, dict):
        candidates.append(value)
    for candidate in candidates:
        for key in ("file_name", "filename", "name"):
            name = candidate.get(key)
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _message_text(message: CanonicalMessage) -> tuple[str, str]:
    content: list[str] = []
    reasoning: list[str] = []
    non_text_kinds: list[str] = []
    for block in message.content:
        text = block.text if isinstance(block.text, str) else ""
        raw = block.raw if isinstance(block.raw, dict) else {}
        if block.kind in {"thinking", "reasoning"}:
            fallback = str(raw.get("thinking") or raw.get("reasoning") or "")
            thought = text if text.strip() else fallback
            if thought.strip():
                reasoning.append(thought)
            continue
        if text.strip():
            content.append(text)
            continue
        attachment_name = _attachment_name(block)
        if attachment_name:
            content.append(f"【附件：{attachment_name}】")
        else:
            non_text_kinds.append(block.kind or "unknown")
    if not content:
        kinds = "、".join(dict.fromkeys(non_text_kinds))
        content.append(f"【外部消息仅包含非文本内容：{kinds or 'unknown'}】")
    return "\n\n".join(content), "\n\n".join(reasoning)


def _stored_role(role: str) -> str:
    return role if role in {"user", "assistant", "system"} else "system"


class ConversationImportService:
    def __init__(self, repository: ImportBatchRepository, registry: ImporterRegistry):
        self.repository = repository
        self.registry = registry

    def preview(
        self,
        payload: Any,
        *,
        source_name: str | None = None,
        format_hint: str | None = None,
    ) -> dict[str, Any]:
        canonical = self.registry.parse(
            payload,
            source_name=source_name,
            format_hint=format_hint,
        )
        batch_id = str(uuid.uuid4())
        duplicates = self._duplicate_conversation_ids(canonical)
        items = self._preview_items(canonical, duplicates)
        message_count = sum(item["message_count"] for item in items)
        batch = self.repository.create_preview(
            batch_id,
            canonical,
            conversation_count=len(items),
            message_count=message_count,
            created_at=now_iso(),
        )
        return {**batch, **self._preview_payload(canonical, items)}

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        batch, canonical = self.repository.load_canonical(batch_id)
        payload = self.repository.get_batch(batch_id)
        duplicates = self._duplicate_conversation_ids(
            canonical,
            exclude_batch_id=batch_id,
        )
        payload.update(self._preview_payload(canonical, duplicate_ids=duplicates))
        payload["status"] = batch["status"]
        return payload

    def list_batches(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_batches(limit)

    def commit(
        self,
        batch_id: str,
        *,
        selected_conversation_ids: list[str] | None = None,
        persona_mapping: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        row, canonical = self.repository.load_canonical(batch_id)
        if row["status"] == "committed":
            return self.get_batch(batch_id)
        available_ids = {item.id for item in canonical.conversations}
        duplicates = self._duplicate_conversation_ids(
            canonical,
            exclude_batch_id=batch_id,
        )
        if selected_conversation_ids is None:
            selected_ids = {
                conversation.id
                for conversation in canonical.conversations
                if not duplicates.get(conversation.provenance.conversation_id or "")
            }
        else:
            selected_ids = {
                str(conversation_id).strip()
                for conversation_id in selected_conversation_ids
            }
            unknown_ids = sorted(selected_ids - available_ids)
            if unknown_ids:
                raise ImportBatchConflict(
                    "所选会话不在该导入批次中：" + ", ".join(unknown_ids)
                )
        normalized_mapping = {
            str(identity_id).strip(): (
                str(persona_id).strip() if persona_id is not None else None
            )
            for identity_id, persona_id in (persona_mapping or {}).items()
        }
        known_identity_ids = {
            identity["id"]
            for conversation in canonical.conversations
            for identity in self._source_identities(canonical.platform, conversation)
        }
        unknown_identity_ids = sorted(set(normalized_mapping) - known_identity_ids)
        if unknown_identity_ids:
            raise ImportBatchConflict(
                "来源身份不在该导入批次中："
                + ", ".join(unknown_identity_ids)
            )
        required_persona_ids = tuple(
            sorted(
                {
                    persona_id
                    for persona_id in normalized_mapping.values()
                    if persona_id
                }
            )
        )
        plans = self._plans(canonical, selected_ids, normalized_mapping)
        committed = self.repository.commit_batch(
            batch_id,
            plans,
            committed_at=now_iso(),
            required_persona_ids=required_persona_ids,
        )
        committed.update(self._preview_payload(canonical, duplicate_ids=duplicates))
        committed["commit_selection"] = {
            "selected_conversation_ids": [
                conversation.id
                for conversation in canonical.conversations
                if conversation.id in selected_ids
            ],
            "persona_mapping": normalized_mapping,
            "created_conversation_ids": committed["created_conversation_ids"],
        }
        return committed

    def rollback(self, batch_id: str) -> dict[str, Any]:
        return self.repository.rollback_batch(batch_id, rolled_back_at=now_iso())

    def conversation_provenance(self, conversation_id: str) -> dict[str, Any]:
        return self.repository.conversation_provenance(conversation_id)

    def _duplicate_conversation_ids(
        self,
        canonical: CanonicalImportBatch,
        *,
        exclude_batch_id: str | None = None,
    ) -> dict[str, list[str]]:
        source_ids = {
            source_conversation_id
            for conversation in canonical.conversations
            if (
                source_conversation_id := conversation.provenance.conversation_id
            )
        }
        return self.repository.committed_conversation_ids_by_source(
            canonical.platform,
            source_ids,
            exclude_batch_id=exclude_batch_id,
        )

    @classmethod
    def _preview_payload(
        cls,
        canonical: CanonicalImportBatch,
        items: list[dict[str, Any]] | None = None,
        *,
        duplicate_ids: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        summaries = (
            items
            if items is not None
            else cls._preview_items(canonical, duplicate_ids)
        )
        role_counts: Counter[str] = Counter()
        identity_index: dict[str, dict[str, Any]] = {}
        for summary in summaries:
            role_counts.update(summary["role_counts"])
            for identity in summary["source_identities"]:
                identity_id = str(identity["id"])
                existing = identity_index.get(identity_id)
                if existing is None:
                    existing = {
                        **identity,
                        "roles": list(identity["roles"]),
                        "conversation_ids": [],
                    }
                    identity_index[identity_id] = existing
                else:
                    existing["message_count"] += identity["message_count"]
                    existing["roles"] = sorted(
                        set(existing["roles"]) | set(identity["roles"])
                    )
                existing["conversation_ids"].append(
                    summary["canonical_conversation_id"]
                )
        source_identities = list(identity_index.values())
        selected_ids = [
            item["canonical_conversation_id"]
            for item in summaries
            if not item["possible_duplicate"]
        ]
        excluded_ids = [
            item["canonical_conversation_id"]
            for item in summaries
            if item["possible_duplicate"]
        ]
        statistics = {
            "conversation_count": len(summaries),
            "message_count": sum(item["message_count"] for item in summaries),
            "role_counts": dict(sorted(role_counts.items())),
            "branch_count": sum(item["branch_count"] for item in summaries),
            "non_text_block_count": sum(
                item["non_text_block_count"] for item in summaries
            ),
            "attachment_count": sum(
                item["attachment_count"] for item in summaries
            ),
            "warning_count": len(canonical.warnings),
            "source_identity_count": len(source_identities),
            "selected_conversation_count": len(selected_ids),
            "possible_duplicate_count": len(excluded_ids),
        }
        return {
            # `preview` remains for existing clients; the explicit name is the
            # stable React contract.
            "preview": summaries,
            "conversation_summaries": summaries,
            "source_identities": source_identities,
            "persona_mapping_defaults": {
                identity["id"]: None for identity in source_identities
            },
            "statistics": statistics,
            "selection": {
                "default": "all",
                "selected_conversation_ids": selected_ids,
                "excluded_conversation_ids": excluded_ids,
            },
        }

    @classmethod
    def _preview_items(
        cls,
        canonical: CanonicalImportBatch,
        duplicate_ids: dict[str, list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        duplicate_ids = duplicate_ids or {}
        items: list[dict[str, Any]] = []
        for conversation in canonical.conversations:
            messages = [node for node in conversation.nodes if node.message is not None]
            role_counts = Counter(node.message.role for node in messages)
            non_text = sum(
                1
                for node in messages
                for block in node.message.content
                if not (isinstance(block.text, str) and block.text.strip())
                and block.kind not in {"thinking", "reasoning"}
            )
            attachments = sum(
                1
                for node in messages
                for block in node.message.content
                if cls._is_attachment_block(block)
            )
            source_ids = {
                identity
                for identity in (
                    conversation.id,
                    conversation.provenance.conversation_id,
                )
                if identity is not None
            }
            conversation_warnings = [
                warning
                for warning in canonical.warnings
                if warning.conversation_id in source_ids
            ]
            source_context = conversation.metadata.get("source_context", {})
            if not isinstance(source_context, dict):
                source_context = {}
            source_conversation_id = conversation.provenance.conversation_id
            existing_conversation_ids = list(
                duplicate_ids.get(source_conversation_id or "", ())
            )
            items.append(
                {
                    "canonical_conversation_id": conversation.id,
                    "source_conversation_id": source_conversation_id,
                    "title": conversation.title,
                    "created_at": conversation.created_at,
                    "updated_at": conversation.updated_at,
                    "message_count": len(messages),
                    "role_counts": dict(sorted(role_counts.items())),
                    "branch_count": len(conversation.branches),
                    "non_text_block_count": non_text,
                    "attachment_count": attachments,
                    "warning_count": len(conversation_warnings),
                    "warning_codes": [
                        warning.code for warning in conversation_warnings
                    ],
                    "selected": not existing_conversation_ids,
                    "possible_duplicate": bool(existing_conversation_ids),
                    "existing_conversation_ids": existing_conversation_ids,
                    "source_context": source_context,
                    "source_identities": cls._source_identities(
                        canonical.platform, conversation
                    ),
                }
            )
        return items

    @staticmethod
    def _is_attachment_block(block: CanonicalContentBlock) -> bool:
        kind = (block.kind or "").lower()
        return bool(
            block.mime_type
            or kind in {
                "attachment",
                "file",
                "image",
                "image_asset_pointer",
                "audio",
                "audio_asset_pointer",
                "video",
            }
        )

    @classmethod
    def _source_identities(
        cls,
        platform: str,
        conversation: CanonicalConversation,
    ) -> list[dict[str, Any]]:
        identities: dict[str, dict[str, Any]] = {}
        participants = getattr(conversation, "participants", ()) or ()
        for participant in participants:
            payload = cls._participant_payload(platform, participant)
            identities[payload["id"]] = payload

        for node in conversation.nodes:
            message = node.message
            if message is None:
                continue
            author = getattr(message, "author", None)
            if author is not None:
                payload = cls._participant_payload(platform, author)
            else:
                payload = cls._role_identity_payload(platform, message)
            identity = identities.setdefault(payload["id"], payload)
            identity["message_count"] += 1
            role = str(message.role)
            if role not in identity["roles"]:
                identity["roles"].append(role)

        return [
            {
                **identity,
                "roles": sorted(identity["roles"]),
            }
            for identity in identities.values()
        ]

    @staticmethod
    def _participant_payload(platform: str, participant: Any) -> dict[str, Any]:
        participant_id = str(getattr(participant, "id", "") or "").strip()
        platform_id = getattr(participant, "platform_id", None)
        display_name = getattr(participant, "display_name", None)
        kind = str(getattr(participant, "kind", "unknown") or "unknown")
        if not participant_id:
            participant_id = f"{platform}:participant:{platform_id or display_name or kind}"
        metadata = getattr(participant, "metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        provenance = getattr(participant, "provenance", None)
        if hasattr(provenance, "model_dump"):
            provenance = provenance.model_dump(mode="json")
        elif not isinstance(provenance, dict):
            provenance = {}
        return {
            "id": participant_id,
            "platform": platform,
            "platform_id": str(platform_id) if platform_id is not None else None,
            "kind": kind,
            "display_name": str(display_name) if display_name is not None else None,
            "metadata": metadata,
            "provenance": provenance,
            "derived_from_role": False,
            "message_count": 0,
            "roles": [],
        }

    @staticmethod
    def _role_identity_payload(
        platform: str,
        message: CanonicalMessage,
    ) -> dict[str, Any]:
        source_role = message.provenance.source_role or message.role
        author = message.provenance.extra.get("author")
        author = author if isinstance(author, dict) else {}
        author_id = author.get("id") or author.get("name")
        identity_key = str(author_id or source_role or message.role)
        display_name = author.get("name") or source_role or message.role
        return {
            "id": f"{platform}:role:{identity_key}",
            "platform": platform,
            "platform_id": str(author_id) if author_id is not None else None,
            "kind": str(message.role),
            "display_name": str(display_name),
            "metadata": {},
            "provenance": {},
            "derived_from_role": True,
            "message_count": 0,
            "roles": [],
        }

    def _plans(
        self,
        canonical: CanonicalImportBatch,
        selected_ids: set[str],
        persona_mapping: dict[str, str | None],
    ) -> tuple[ImportConversationPlan, ...]:
        committed_at = datetime.now(timezone.utc)
        plans: list[ImportConversationPlan] = []
        for index, conversation in enumerate(canonical.conversations):
            if conversation.id not in selected_ids:
                continue
            mapped_personas = {
                persona_mapping[identity["id"]]
                for identity in self._source_identities(
                    canonical.platform, conversation
                )
                if persona_mapping.get(identity["id"])
            }
            if len(mapped_personas) > 1:
                raise ImportBatchConflict(
                    f"会话 {conversation.id} 命中多个不同 Persona 映射"
                )
            persona_id = next(iter(mapped_personas), None)
            plans.append(
                self._conversation_plan(
                    conversation,
                    committed_at,
                    index,
                    persona_id=persona_id,
                )
            )
        return tuple(plans)

    def _conversation_plan(
        self,
        conversation: CanonicalConversation,
        committed_at: datetime,
        conversation_index: int,
        *,
        persona_id: str | None,
    ) -> ImportConversationPlan:
        conversation_id = str(uuid.uuid4())
        nodes = sorted(conversation.nodes, key=lambda item: item.source_order)
        node_by_id = {node.id: node for node in nodes}
        imported_ids = {
            node.id: str(uuid.uuid4()) for node in nodes if node.message is not None
        }

        def imported_parent(node: CanonicalMessageNode) -> str | None:
            parent_id = node.parent_id
            visited: set[str] = set()
            while parent_id and parent_id not in visited:
                visited.add(parent_id)
                if parent_id in imported_ids:
                    return imported_ids[parent_id]
                parent = node_by_id.get(parent_id)
                parent_id = parent.parent_id if parent is not None else None
            return None

        messages: list[ImportMessagePlan] = []
        for node in nodes:
            message = node.message
            if message is None:
                continue
            fallback_time = committed_at + timedelta(
                seconds=conversation_index, microseconds=node.source_order
            )
            content, reasoning = _message_text(message)
            messages.append(
                ImportMessagePlan(
                    id=imported_ids[node.id],
                    conversation_id=conversation_id,
                    role=_stored_role(message.role),
                    content=content,
                    provider_id=None,
                    model=None,
                    created_at=message.created_at or fallback_time.isoformat(),
                    reasoning=reasoning,
                    parent_message_id=imported_parent(node),
                    source_conversation_id=conversation.provenance.conversation_id,
                    canonical_conversation_id=conversation.id,
                    source_node_id=node.id,
                    source_message_id=message.provenance.message_id or message.id,
                    provenance_json=_json(node.model_dump(mode="json")),
                )
            )
        current = next(
            (branch for branch in conversation.branches if branch.is_current), None
        )
        selections: dict[str, str] = {}
        if current is not None:
            for node_id in current.node_ids:
                node = node_by_id.get(node_id)
                if node is None or node.message is None:
                    continue
                parent_id = imported_parent(node)
                if node.message.role == "assistant" and parent_id:
                    selections[parent_id] = imported_ids[node.id]
        created_at = conversation.created_at or (
            committed_at + timedelta(seconds=conversation_index)
        ).isoformat()
        updated_at = conversation.updated_at or created_at
        return ImportConversationPlan(
            id=conversation_id,
            title=(conversation.title.strip() or "导入对话")[:100],
            persona_id=persona_id,
            created_at=created_at,
            updated_at=updated_at,
            source_conversation_id=conversation.provenance.conversation_id,
            canonical_conversation_id=conversation.id,
            provenance_json=_json(conversation.model_dump(mode="json")),
            messages=tuple(messages),
            selections=tuple(selections.items()),
        )
