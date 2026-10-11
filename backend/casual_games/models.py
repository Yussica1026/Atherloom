from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ActorKind = Literal["user", "persona"]
SessionStatus = Literal["active", "finished", "abandoned"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        validate_assignment=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class SessionCreateIn(StrictModel):
    game_id: str = Field(min_length=1, max_length=120)
    conversation_id: str = Field(min_length=1, max_length=160)
    options: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @field_validator("game_id", "conversation_id", mode="before")
    @classmethod
    def trim_identifiers(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ActionCommitIn(StrictModel):
    action: dict[str, Any] = Field(min_length=1, max_length=32)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def trim_idempotency_key(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PersonaTurnIn(StrictModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def trim_idempotency_key(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoryDecisionIn(StrictModel):
    approved: bool
    importance: float | None = Field(default=None, ge=0.1, le=1)


class CasualGameSession(StrictModel):
    id: str
    game_id: str
    rules_version: int = Field(ge=1)
    conversation_id: str
    persona_id: str
    player_id: str
    state: dict[str, Any]
    status: SessionStatus
    revision: int = Field(ge=0)
    result_id: str | None = None
    created_at: str
    updated_at: str
    finished_at: str | None = None


class CasualGameHistoryEntry(StrictModel):
    id: str
    session_id: str
    revision: int = Field(ge=1)
    actor_kind: ActorKind
    actor_id: str
    idempotency_key: str
    expected_revision: int = Field(ge=0)
    action: dict[str, Any]
    events: list[dict[str, Any]]
    response: dict[str, Any]
    created_at: str


class CasualGameParticipant(StrictModel):
    kind: ActorKind
    id: str = Field(min_length=1, max_length=160)


class CasualGameResult(StrictModel):
    game_id: str = Field(min_length=1, max_length=120)
    session_id: str = Field(min_length=1, max_length=160)
    outcome: str = Field(min_length=1, max_length=240)
    participants: list[CasualGameParticipant] = Field(min_length=2, max_length=8)
    score: dict[str, int] | None = Field(default=None, max_length=16)
    notable_events: list[str] = Field(default_factory=list, max_length=32)
    started_at: str
    finished_at: str

    @field_validator("notable_events")
    @classmethod
    def validate_notable_event_lengths(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip():
                raise ValueError("notable_events 不能包含空文本")
            if len(value) > 1000:
                raise ValueError("notable_events 单条不能超过 1000 字符")
        return values


class CasualGameResultRecord(StrictModel):
    id: str
    session_id: str
    result: CasualGameResult
    chat_message_id: str | None = None
    memory_id: str | None = None
    memory_decision: Literal["approved", "declined"] | None = None
    created_at: str


class BehaviorConfigUpdate(StrictModel):
    instructions: str | None = Field(default=None, max_length=32_000)
    strategy_instructions: str | None = Field(default=None, max_length=32_000)
    reaction_instructions: str | None = Field(default=None, max_length=32_000)
    memory_instructions: str | None = Field(default=None, max_length=32_000)
    memory_mode: Literal["off", "ask", "auto"] = "ask"


class BehaviorConfig(BehaviorConfigUpdate):
    persona_id: str = Field(min_length=1, max_length=160)
    game_id: str = Field(min_length=1, max_length=120)
    updated_at: str | None = None
