from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable

from backend.game.events import (
    AdvanceQuestEvent,
    AdvanceTimeEvent,
    AdjustRelationshipEvent,
    DiscoverFactEvent,
    MoveActorEvent,
    TransferItemEvent,
    WorldEvent,
    relationship_id_for,
)
from backend.game.models import (
    AcceptQuestIntent,
    ActorPosition,
    AICharacterActionIn,
    DropItemIntent,
    FreeformIntent,
    HelpActorIntent,
    LocationPosition,
    MoveIntent,
    PlayerActionIn,
    QuestState,
    RelationshipState,
    TakeItemIntent,
    WaitIntent,
    WorldState,
)
from backend.game.rules import (
    RuleViolation,
    actor_key,
    actor_location,
    validate_event,
    validate_world_state,
)


def canonical_json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def state_digest(state: WorldState) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


def clone_state(state: WorldState) -> WorldState:
    return WorldState.model_validate_json(canonical_json(state))


def player_action_events(
    state: WorldState,
    action: PlayerActionIn,
    event_id_factory: Callable[[], str] | None = None,
) -> list[WorldEvent]:
    if action.actor.id != state.player.id:
        raise RuleViolation("player_mismatch", "Player 行动者与当前 Session 的真人玩家不一致")
    if isinstance(action.intent, FreeformIntent):
        raise RuleViolation(
            "freeform_requires_game_master",
            "自由行动必须通过 Game Master 生成并校验候选领域事件",
        )
    return _actor_action_events(state, action, event_id_factory)


def ai_character_action_events(
    state: WorldState,
    action: AICharacterActionIn,
    event_id_factory: Callable[[], str] | None = None,
) -> list[WorldEvent]:
    resident = state.ai_characters.get(action.actor.id)
    if resident is None:
        raise RuleViolation("ai_character_not_found", "AICharacter 不属于当前 Session")
    if not resident.active:
        raise RuleViolation("ai_character_inactive", "AICharacter 当前未启用")
    return _actor_action_events(state, action, event_id_factory)


def _actor_action_events(
    state: WorldState,
    action: PlayerActionIn | AICharacterActionIn,
    event_id_factory: Callable[[], str] | None = None,
) -> list[WorldEvent]:
    make_id = event_id_factory or (lambda: str(uuid.uuid4()))
    intent = action.intent
    location_id = actor_location(state, action.actor)
    if isinstance(intent, MoveIntent):
        return [MoveActorEvent(
            event_id=make_id(),
            actor=action.actor,
            from_location_id=location_id,
            to_location_id=intent.destination_id,
        )]
    if isinstance(intent, TakeItemIntent):
        item = state.items.get(intent.item_id)
        if not item:
            raise RuleViolation("item_not_found", f"物品不存在：{intent.item_id}")
        return [TransferItemEvent(
            event_id=make_id(),
            actor=action.actor,
            item_id=item.id,
            from_position=item.position,
            to_position=ActorPosition(actor=action.actor),
        )]
    if isinstance(intent, DropItemIntent):
        item = state.items.get(intent.item_id)
        if not item:
            raise RuleViolation("item_not_found", f"物品不存在：{intent.item_id}")
        return [TransferItemEvent(
            event_id=make_id(),
            actor=action.actor,
            item_id=item.id,
            from_position=item.position,
            to_position=LocationPosition(location_id=location_id),
        )]
    if isinstance(intent, WaitIntent):
        return [AdvanceTimeEvent(
            event_id=make_id(),
            actor=action.actor,
            minutes=intent.minutes,
        )]
    if isinstance(intent, HelpActorIntent):
        return [AdjustRelationshipEvent(
            event_id=make_id(),
            actor=action.actor,
            relationship_id=relationship_id_for(action.actor, intent.target),
            subject=action.actor,
            object=intent.target,
            affinity_delta=5,
            trust_delta=3,
            respect_delta=2,
            cause="help",
        )]
    if isinstance(intent, AcceptQuestIntent):
        quest = state.quests.get(intent.quest_id)
        if not quest:
            raise RuleViolation("quest_not_found", f"任务不存在：{intent.quest_id}")
        return [AdvanceQuestEvent(
            event_id=make_id(),
            actor=action.actor,
            quest_id=quest.id,
            from_status=quest.status,
            to_status="active",
        )]
    raise RuleViolation("unsupported_actor_intent", "当前阶段不支持这个角色行动")


def apply_event_unchecked(state: WorldState, event: WorldEvent) -> None:
    if isinstance(event, MoveActorEvent):
        if event.actor.kind == "player":
            state.player.location_id = event.to_location_id
            state.locations[event.to_location_id].visited = True
        elif event.actor.kind == "ai_character":
            state.ai_characters[event.actor.id].location_id = event.to_location_id
        else:
            state.npcs[event.actor.id].location_id = event.to_location_id
        return
    if isinstance(event, TransferItemEvent):
        state.items[event.item_id].position = event.to_position
        return
    if isinstance(event, AdvanceTimeEvent):
        state.clock.total_minutes += event.minutes
        return
    if isinstance(event, DiscoverFactEvent):
        known_by = state.facts[event.fact_id].known_by
        if actor_key(event.actor) not in {actor_key(actor) for actor in known_by}:
            known_by.append(event.actor)
        return
    if isinstance(event, AdjustRelationshipEvent):
        existing = state.relationships.get(event.relationship_id)
        if not existing:
            existing = RelationshipState(
                id=event.relationship_id,
                subject=event.subject,
                object=event.object,
            )
            state.relationships[event.relationship_id] = existing
        existing.affinity += event.affinity_delta
        existing.trust += event.trust_delta
        existing.fear += event.fear_delta
        existing.respect += event.respect_delta
        return
    if isinstance(event, AdvanceQuestEvent):
        state.quests[event.quest_id].status = event.to_status
        return
    raise RuleViolation("unsupported_event", "不支持的领域事件")


def validate_and_apply_events(
    state: WorldState,
    events: Iterable[WorldEvent],
    next_revision: int,
) -> WorldState:
    event_list = list(events)
    if not event_list:
        raise RuleViolation("empty_event_batch", "一个已提交回合必须至少包含一个领域事件")
    if next_revision != state.revision + 1:
        raise RuleViolation("revision_gap", "领域事件只能提交到紧邻的下一个 revision")
    working = clone_state(state)
    validate_world_state(working)
    for event in event_list:
        validate_event(working, event)
        apply_event_unchecked(working, event)
    working.revision = next_revision
    working.turn += 1
    validate_world_state(working)
    return working
