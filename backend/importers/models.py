from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SourcePlatform = Literal["chatgpt", "claude", "astrbot", "kelivo"]
CanonicalRole = Literal["user", "assistant", "system", "tool", "unknown"]


class CanonicalModel(BaseModel):
    """Strict output model; source-data tolerance belongs in the parsers."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        validate_assignment=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class SourceProvenance(CanonicalModel):
    platform: SourcePlatform
    format_id: str
    source_name: str | None = None
    conversation_id: str | None = None
    node_id: str | None = None
    message_id: str | None = None
    branch_id: str | None = None
    source_role: str | None = None
    source_index: int | None = Field(default=None, ge=0)
    extra: dict[str, Any] = Field(default_factory=dict)


class CanonicalContentBlock(CanonicalModel):
    kind: str
    text: str | None = None
    mime_type: str | None = None
    raw: Any = None
    provenance: SourceProvenance


class CanonicalParticipant(CanonicalModel):
    """An identity explicitly present in the source export.

    ``kind`` intentionally remains open-ended: exports distinguish personas,
    assistants, platform sessions, and sometimes actual people.  Importers do
    not upgrade a session or group identifier into a person without evidence.
    """

    id: str
    kind: str
    display_name: str | None = None
    platform_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: SourceProvenance


class CanonicalMessage(CanonicalModel):
    id: str
    role: CanonicalRole
    created_at: str | None = None
    updated_at: str | None = None
    content: list[CanonicalContentBlock] = Field(default_factory=list)
    author: CanonicalParticipant | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: SourceProvenance


class CanonicalMessageNode(CanonicalModel):
    id: str
    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)
    message: CanonicalMessage | None = None
    source_order: int = Field(ge=0)
    provenance: SourceProvenance


class CanonicalBranch(CanonicalModel):
    id: str
    node_ids: list[str]
    leaf_node_id: str
    is_current: bool = False
    source_branch_id: str | None = None


class CanonicalConversation(CanonicalModel):
    id: str
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    current_node_id: str | None = None
    root_node_ids: list[str] = Field(default_factory=list)
    nodes: list[CanonicalMessageNode] = Field(default_factory=list)
    branches: list[CanonicalBranch] = Field(default_factory=list)
    participants: list[CanonicalParticipant] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: SourceProvenance


class ImportWarning(CanonicalModel):
    code: str
    message: str
    conversation_id: str | None = None
    node_id: str | None = None


class CanonicalImportBatch(CanonicalModel):
    format_id: str
    platform: SourcePlatform
    conversations: list[CanonicalConversation] = Field(default_factory=list)
    warnings: list[ImportWarning] = Field(default_factory=list)
    provenance: SourceProvenance
