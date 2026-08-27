from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter

from backend.game.models import ActorRef, ENTITY_ID_PATTERN, ItemPosition, StrictModel


def relationship_id_for(subject: ActorRef, object: ActorRef) -> str:
    """Return the one canonical directed relationship ID for an actor pair."""

    identity = json.dumps(
        [[subject.kind, subject.id], [object.kind, object.id]],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return f"rel:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


class MoveActorEvent(StrictModel):
    type: Literal["move_actor"] = "move_actor"
    event_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    actor: ActorRef
    from_location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    to_location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class TransferItemEvent(StrictModel):
    type: Literal["transfer_item"] = "transfer_item"
    event_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    actor: ActorRef
    item_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    from_position: ItemPosition
    to_position: ItemPosition


class AdvanceTimeEvent(StrictModel):
    type: Literal["advance_time"] = "advance_time"
    event_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    actor: ActorRef
    minutes: int = Field(ge=1, le=1440)


class DiscoverFactEvent(StrictModel):
    type: Literal["discover_fact"] = "discover_fact"
    event_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    actor: ActorRef
    fact_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class AdjustRelationshipEvent(StrictModel):
    type: Literal["adjust_relationship"] = "adjust_relationship"
    event_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    actor: ActorRef
    relationship_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    subject: ActorRef
    object: ActorRef
    affinity_delta: int = Field(default=0, ge=-10, le=10)
    trust_delta: int = Field(default=0, ge=-10, le=10)
    fear_delta: int = Field(default=0, ge=-10, le=10)
    respect_delta: int = Field(default=0, ge=-10, le=10)
    cause: Literal["conversation", "help", "harm", "gift", "promise", "betrayal", "other"]


class AdvanceQuestEvent(StrictModel):
    type: Literal["advance_quest"] = "advance_quest"
    event_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    actor: ActorRef
    quest_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    from_status: Literal["hidden", "available", "active", "completed", "failed"]
    to_status: Literal["hidden", "available", "active", "completed", "failed"]


WorldEvent: TypeAlias = Annotated[
    MoveActorEvent
    | TransferItemEvent
    | AdvanceTimeEvent
    | DiscoverFactEvent
    | AdjustRelationshipEvent
    | AdvanceQuestEvent,
    Field(discriminator="type"),
]

WORLD_EVENT_ADAPTER = TypeAdapter(WorldEvent)


def parse_world_event(value: object) -> WorldEvent:
    return WORLD_EVENT_ADAPTER.validate_python(value)
