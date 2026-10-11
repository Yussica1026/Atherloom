from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


ImportBatchStatus = Literal["previewed", "committed", "rolled_back"]


class ImportPreviewIn(BaseModel):
    payload: Any
    source_name: str | None = Field(default=None, max_length=500)
    format_hint: str | None = Field(default=None, min_length=1, max_length=100)


class ImportEncodedPreviewIn(BaseModel):
    source_name: str = Field(min_length=1, max_length=500)
    content_base64: str
    format_hint: str | None = Field(default=None, min_length=1, max_length=100)


class ImportCommitIn(BaseModel):
    selected_conversation_ids: list[str] | None = None
    persona_mapping: dict[str, str | None] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImportMessagePlan:
    id: str
    conversation_id: str
    role: str
    content: str
    provider_id: None
    model: None
    created_at: str
    reasoning: str
    parent_message_id: str | None
    source_conversation_id: str | None
    canonical_conversation_id: str
    source_node_id: str
    source_message_id: str | None
    provenance_json: str


@dataclass(frozen=True, slots=True)
class ImportConversationPlan:
    id: str
    title: str
    persona_id: str | None
    created_at: str
    updated_at: str
    source_conversation_id: str | None
    canonical_conversation_id: str
    provenance_json: str
    messages: tuple[ImportMessagePlan, ...]
    selections: tuple[tuple[str, str], ...]
