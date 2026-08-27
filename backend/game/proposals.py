from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter

from backend.game.events import WorldEvent, parse_world_event, relationship_id_for
from backend.game.models import (
    ActorRef,
    ENTITY_ID_PATTERN,
    ItemPosition,
    PlayerActionIn,
    StrictModel,
    TurnNarration,
    WorldState,
)
from backend.game.rules import RuleViolation, actor_key


STATE_HASH_PATTERN = r"^[0-9a-f]{64}$"
MAX_PROPOSED_EVENTS = 16


class ProposedMoveActorEvent(StrictModel):
    type: Literal["move_actor"] = "move_actor"
    actor: ActorRef
    from_location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    to_location_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class ProposedTransferItemEvent(StrictModel):
    type: Literal["transfer_item"] = "transfer_item"
    actor: ActorRef
    item_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    from_position: ItemPosition
    to_position: ItemPosition


class ProposedAdvanceTimeEvent(StrictModel):
    type: Literal["advance_time"] = "advance_time"
    actor: ActorRef
    minutes: int = Field(ge=1, le=1440)


class ProposedDiscoverFactEvent(StrictModel):
    type: Literal["discover_fact"] = "discover_fact"
    actor: ActorRef
    fact_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)


class ProposedAdjustRelationshipEvent(StrictModel):
    type: Literal["adjust_relationship"] = "adjust_relationship"
    actor: ActorRef
    subject: ActorRef
    object: ActorRef
    affinity_delta: int = Field(default=0, ge=-10, le=10)
    trust_delta: int = Field(default=0, ge=-10, le=10)
    fear_delta: int = Field(default=0, ge=-10, le=10)
    respect_delta: int = Field(default=0, ge=-10, le=10)
    cause: Literal["conversation", "help", "harm", "gift", "promise", "betrayal", "other"]


class ProposedAdvanceQuestEvent(StrictModel):
    type: Literal["advance_quest"] = "advance_quest"
    actor: ActorRef
    quest_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    from_status: Literal["hidden", "available", "active", "completed", "failed"]
    to_status: Literal["hidden", "available", "active", "completed", "failed"]


ProposedWorldEvent: TypeAlias = Annotated[
    ProposedMoveActorEvent
    | ProposedTransferItemEvent
    | ProposedAdvanceTimeEvent
    | ProposedDiscoverFactEvent
    | ProposedAdjustRelationshipEvent
    | ProposedAdvanceQuestEvent,
    Field(discriminator="type"),
]

PROPOSED_WORLD_EVENT_ADAPTER = TypeAdapter(ProposedWorldEvent)


class TurnContext(StrictModel):
    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    based_on_revision: int = Field(ge=0)
    based_on_state_hash: str = Field(pattern=STATE_HASH_PATTERN)
    action: PlayerActionIn
    state: WorldState


class TurnProposal(StrictModel):
    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1, max_length=120, pattern=ENTITY_ID_PATTERN)
    based_on_revision: int = Field(ge=0)
    based_on_state_hash: str = Field(pattern=STATE_HASH_PATTERN)
    initiator: ActorRef
    events: list[ProposedWorldEvent] = Field(
        min_length=1,
        max_length=MAX_PROPOSED_EVENTS,
    )
    narration: TurnNarration


def parse_proposed_world_event(value: object) -> ProposedWorldEvent:
    return PROPOSED_WORLD_EVENT_ADAPTER.validate_python(value)


def parse_turn_proposal(value: object) -> TurnProposal:
    if isinstance(value, str):
        return TurnProposal.model_validate_json(value)
    return TurnProposal.model_validate(value)


def _same_actor(left: ActorRef, right: ActorRef) -> bool:
    return actor_key(left) == actor_key(right)


def validate_turn_proposal_basis(
    context: TurnContext,
    proposal: TurnProposal,
) -> None:
    """Bind an untrusted GM envelope to one immutable state basis."""

    action = context.action
    if context.session_id != context.state.session_id:
        raise RuleViolation("context_session_mismatch", "TurnContext 与 WorldState 的 Session 不一致")
    if context.based_on_revision != context.state.revision:
        raise RuleViolation("context_revision_mismatch", "TurnContext 与 WorldState 的 revision 不一致")
    if action.expected_revision != context.based_on_revision:
        raise RuleViolation("action_revision_mismatch", "Player 行动与 TurnContext 的 revision 不一致")
    if action.actor.id != context.state.player.id:
        raise RuleViolation("player_mismatch", "Player 行动者与当前 Session 的真人玩家不一致")

    if proposal.session_id != context.session_id:
        raise RuleViolation("proposal_session_mismatch", "候选变化属于另一个 Session")
    if proposal.based_on_revision != context.based_on_revision:
        raise RuleViolation("proposal_revision_mismatch", "候选变化基于错误的 WorldState revision")
    if proposal.based_on_state_hash != context.based_on_state_hash:
        raise RuleViolation("proposal_state_hash_mismatch", "候选变化基于错误的 WorldState hash")
    if not _same_actor(proposal.initiator, action.actor):
        raise RuleViolation("proposal_initiator_mismatch", "候选变化的发起者与 Player 行动不一致")
    for event in proposal.events:
        if not _same_actor(event.actor, proposal.initiator):
            raise RuleViolation(
                "proposal_actor_not_authorized",
                "Player 回合不能借候选事件操纵 NPC、AICharacter 或其他 Player",
            )


def materialize_turn_proposal(
    proposal: TurnProposal,
    event_id_factory: Callable[[], str] | None = None,
) -> list[WorldEvent]:
    """Create authoritative events and server-owned IDs from GM candidates."""

    make_id = event_id_factory or (lambda: f"event-{uuid.uuid4().hex}")
    events: list[WorldEvent] = []
    seen_ids: set[str] = set()
    for proposed in proposal.events:
        event_id = make_id()
        if event_id in seen_ids:
            raise RuleViolation("duplicate_event_id", "服务端生成了重复的领域事件 ID")
        seen_ids.add(event_id)
        payload = proposed.model_dump(mode="json")
        if isinstance(proposed, ProposedAdjustRelationshipEvent):
            payload["relationship_id"] = relationship_id_for(
                proposed.subject,
                proposed.object,
            )
        payload["event_id"] = event_id
        events.append(parse_world_event(payload))
    return events
