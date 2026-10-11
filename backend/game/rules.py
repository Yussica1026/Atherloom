from __future__ import annotations

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
    ActorRef,
    AICharacterActionIn,
    DropItemIntent,
    FreeformIntent,
    HelpActorIntent,
    ItemPosition,
    LocationPosition,
    MoveIntent,
    PlayerActionIn,
    TakeItemIntent,
    WaitIntent,
    WorldState,
)


MAX_DISCOVERED_FACTS_PER_PLAYER_TURN = 3
MAX_DISCOVERED_FACTS_PER_AI_CHARACTER_TURN = 3
MAX_FREEFORM_EVENTS_PER_PLAYER_TURN = 6


class RuleViolation(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def actor_key(actor: ActorRef) -> tuple[str, str]:
    return actor.kind, actor.id


def actor_exists(state: WorldState, actor: ActorRef) -> bool:
    if actor.kind == "player":
        return state.player.id == actor.id
    if actor.kind == "ai_character":
        return actor.id in state.ai_characters
    return actor.id in state.npcs


def actor_location(state: WorldState, actor: ActorRef) -> str:
    if actor.kind == "player" and state.player.id == actor.id:
        return state.player.location_id
    if actor.kind == "ai_character" and actor.id in state.ai_characters:
        return state.ai_characters[actor.id].location_id
    if actor.kind == "npc" and actor.id in state.npcs:
        return state.npcs[actor.id].location_id
    raise RuleViolation("actor_not_found", f"行动者不存在：{actor.kind}:{actor.id}")


def position_equal(left: ItemPosition, right: ItemPosition) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def validate_player_event_authority(
    state: WorldState,
    action: PlayerActionIn,
    events: list[WorldEvent],
) -> None:
    """Bind authoritative events to one concrete Player command.

    This policy is intentionally separate from ``validate_event``: domain
    rules decide whether a fact change is possible, while this function
    decides whether this Player turn is allowed to submit that change.
    """

    if action.expected_revision != state.revision:
        raise RuleViolation("action_revision_mismatch", "Player 行动与 WorldState 的 revision 不一致")
    if action.actor.id != state.player.id:
        raise RuleViolation("player_mismatch", "Player 行动者与当前 Session 的真人玩家不一致")
    for event in events:
        if actor_key(event.actor) != actor_key(action.actor):
            raise RuleViolation(
                "proposal_actor_not_authorized",
                "Player 回合不能借候选事件操纵 NPC、AICharacter 或其他 Player",
            )

    def require_exactly_one(event_type: type, intent_name: str) -> WorldEvent:
        matches = [event for event in events if isinstance(event, event_type)]
        if len(matches) != 1:
            raise RuleViolation(
                "proposal_primary_event_mismatch",
                f"{intent_name} 候选必须且只能包含一个对应的主领域事件",
            )
        return matches[0]

    intent = action.intent
    if isinstance(intent, FreeformIntent):
        if len(events) > MAX_FREEFORM_EVENTS_PER_PLAYER_TURN:
            raise RuleViolation(
                "freeform_event_budget_exceeded",
                "单个自由行动回合最多只能提交六个候选领域事件",
            )
        allowed_types = (
            MoveActorEvent,
            TransferItemEvent,
            AdvanceTimeEvent,
            DiscoverFactEvent,
            AdjustRelationshipEvent,
            AdvanceQuestEvent,
        )
        for event in events:
            if not isinstance(event, allowed_types):
                raise RuleViolation(
                    "proposal_event_not_authorized",
                    f"自由行动不能提交 {event.type} 候选变化",
                )
        return

    primary_type: type
    if isinstance(intent, MoveIntent):
        primary_type = MoveActorEvent
        primary = require_exactly_one(primary_type, "移动")
        assert isinstance(primary, MoveActorEvent)
        if (
            primary.from_location_id != state.player.location_id
            or primary.to_location_id != intent.destination_id
        ):
            raise RuleViolation("proposal_intent_mismatch", "移动候选与 Player 请求的起点或终点不一致")
    elif isinstance(intent, TakeItemIntent):
        primary_type = TransferItemEvent
        primary = require_exactly_one(primary_type, "拾取物品")
        assert isinstance(primary, TransferItemEvent)
        if primary.item_id != intent.item_id:
            raise RuleViolation("proposal_intent_mismatch", "拾取候选修改了另一个物品")
        if not isinstance(primary.to_position, ActorPosition) or actor_key(
            primary.to_position.actor
        ) != actor_key(action.actor):
            raise RuleViolation("proposal_intent_mismatch", "拾取候选必须把物品交给行动者本人")
    elif isinstance(intent, DropItemIntent):
        primary_type = TransferItemEvent
        primary = require_exactly_one(primary_type, "丢下物品")
        assert isinstance(primary, TransferItemEvent)
        if primary.item_id != intent.item_id:
            raise RuleViolation("proposal_intent_mismatch", "丢弃候选修改了另一个物品")
        if not isinstance(primary.from_position, ActorPosition) or actor_key(
            primary.from_position.actor
        ) != actor_key(action.actor):
            raise RuleViolation("proposal_intent_mismatch", "丢弃候选只能使用行动者自己的库存")
        if (
            not isinstance(primary.to_position, LocationPosition)
            or primary.to_position.location_id != state.player.location_id
        ):
            raise RuleViolation("proposal_intent_mismatch", "丢弃候选必须落在行动者当前地点")
    elif isinstance(intent, WaitIntent):
        primary_type = AdvanceTimeEvent
        primary = require_exactly_one(primary_type, "等待")
        assert isinstance(primary, AdvanceTimeEvent)
        if primary.minutes != intent.minutes:
            raise RuleViolation("proposal_intent_mismatch", "等待候选的时间与 Player 请求不一致")
    elif isinstance(intent, HelpActorIntent):
        primary_type = AdjustRelationshipEvent
        primary = require_exactly_one(primary_type, "帮助角色")
        assert isinstance(primary, AdjustRelationshipEvent)
        if (
            actor_key(primary.subject) != actor_key(action.actor)
            or actor_key(primary.object) != actor_key(intent.target)
            or primary.cause != "help"
        ):
            raise RuleViolation("proposal_intent_mismatch", "帮助候选的关系方向、目标或原因不一致")
        if not (
            0 <= primary.affinity_delta <= 5
            and 0 <= primary.trust_delta <= 3
            and -2 <= primary.fear_delta <= 0
            and 0 <= primary.respect_delta <= 2
        ):
            raise RuleViolation("proposal_help_delta_out_of_policy", "帮助事件的关系变化超出 Player 权限预算")
    elif isinstance(intent, AcceptQuestIntent):
        primary_type = AdvanceQuestEvent
        primary = require_exactly_one(primary_type, "接受任务")
        assert isinstance(primary, AdvanceQuestEvent)
        quest = state.quests.get(intent.quest_id)
        if (
            primary.quest_id != intent.quest_id
            or quest is None
            or quest.status != "available"
            or primary.from_status != quest.status
            or primary.to_status != "active"
        ):
            raise RuleViolation("proposal_intent_mismatch", "任务候选与 Player 请求或当前任务状态不一致")
    else:
        raise RuleViolation("unsupported_player_intent", "当前阶段不支持这个 Player 行动")

    fact_consequences: list[DiscoverFactEvent] = []
    for event in events:
        if isinstance(event, primary_type):
            continue
        if not isinstance(event, DiscoverFactEvent):
            raise RuleViolation(
                "proposal_event_not_authorized",
                f"{intent.type} 行动不能提交 {event.type} 候选变化",
            )
        fact_consequences.append(event)
    if len(fact_consequences) > MAX_DISCOVERED_FACTS_PER_PLAYER_TURN:
        raise RuleViolation("proposal_consequence_budget_exceeded", "单个 Player 回合最多发现三个事实")
    fact_ids = [event.fact_id for event in fact_consequences]
    if len(fact_ids) != len(set(fact_ids)):
        raise RuleViolation("duplicate_fact_consequence", "同一回合不能重复发现同一个事实")


def validate_ai_character_event_authority(
    state: WorldState,
    action: AICharacterActionIn,
    events: list[WorldEvent],
) -> None:
    """Bind events to one active AI resident without granting Player/NPC authority."""

    if action.expected_revision != state.revision:
        raise RuleViolation(
            "action_revision_mismatch",
            "AICharacter 行动与 WorldState 的 revision 不一致",
        )
    resident = state.ai_characters.get(action.actor.id)
    if resident is None:
        raise RuleViolation("ai_character_not_found", "AICharacter 不属于当前 Session")
    if not resident.active:
        raise RuleViolation("ai_character_inactive", "AICharacter 当前未启用")
    for event in events:
        if actor_key(event.actor) != actor_key(action.actor):
            raise RuleViolation(
                "proposal_actor_not_authorized",
                "AICharacter 回合不能操纵 Player、NPC 或其他 AICharacter",
            )

    def require_exactly_one(event_type: type, intent_name: str) -> WorldEvent:
        matches = [event for event in events if isinstance(event, event_type)]
        if len(matches) != 1:
            raise RuleViolation(
                "proposal_primary_event_mismatch",
                f"AICharacter {intent_name}候选必须且只能包含一个对应的主领域事件",
            )
        return matches[0]

    intent = action.intent
    primary_type: type
    if isinstance(intent, MoveIntent):
        primary_type = MoveActorEvent
        primary = require_exactly_one(primary_type, "移动")
        assert isinstance(primary, MoveActorEvent)
        if (
            primary.from_location_id != resident.location_id
            or primary.to_location_id != intent.destination_id
        ):
            raise RuleViolation(
                "proposal_intent_mismatch",
                "移动候选与 AICharacter 请求的起点或终点不一致",
            )
    elif isinstance(intent, TakeItemIntent):
        primary_type = TransferItemEvent
        primary = require_exactly_one(primary_type, "拾取物品")
        assert isinstance(primary, TransferItemEvent)
        if primary.item_id != intent.item_id:
            raise RuleViolation("proposal_intent_mismatch", "拾取候选修改了另一个物品")
        if not isinstance(primary.to_position, ActorPosition) or actor_key(
            primary.to_position.actor
        ) != actor_key(action.actor):
            raise RuleViolation("proposal_intent_mismatch", "拾取候选必须把物品交给行动者本人")
    elif isinstance(intent, DropItemIntent):
        primary_type = TransferItemEvent
        primary = require_exactly_one(primary_type, "丢下物品")
        assert isinstance(primary, TransferItemEvent)
        if primary.item_id != intent.item_id:
            raise RuleViolation("proposal_intent_mismatch", "丢弃候选修改了另一个物品")
        if not isinstance(primary.from_position, ActorPosition) or actor_key(
            primary.from_position.actor
        ) != actor_key(action.actor):
            raise RuleViolation("proposal_intent_mismatch", "丢弃候选只能使用行动者自己的库存")
        if (
            not isinstance(primary.to_position, LocationPosition)
            or primary.to_position.location_id != resident.location_id
        ):
            raise RuleViolation("proposal_intent_mismatch", "丢弃候选必须落在行动者当前地点")
    elif isinstance(intent, HelpActorIntent):
        primary_type = AdjustRelationshipEvent
        primary = require_exactly_one(primary_type, "帮助角色")
        assert isinstance(primary, AdjustRelationshipEvent)
        if (
            actor_key(primary.subject) != actor_key(action.actor)
            or actor_key(primary.object) != actor_key(intent.target)
            or primary.cause != "help"
        ):
            raise RuleViolation("proposal_intent_mismatch", "帮助候选的关系方向、目标或原因不一致")
        if not (
            0 <= primary.affinity_delta <= 5
            and 0 <= primary.trust_delta <= 3
            and -2 <= primary.fear_delta <= 0
            and 0 <= primary.respect_delta <= 2
        ):
            raise RuleViolation(
                "proposal_help_delta_out_of_policy",
                "帮助事件的关系变化超出 AICharacter 权限预算",
            )
    else:
        raise RuleViolation("unsupported_ai_character_intent", "当前阶段不支持这个 AICharacter 行动")

    fact_consequences: list[DiscoverFactEvent] = []
    for event in events:
        if isinstance(event, primary_type):
            continue
        if not isinstance(event, DiscoverFactEvent):
            raise RuleViolation(
                "proposal_event_not_authorized",
                f"{intent.type} 行动不能提交 {event.type} 候选变化",
            )
        fact_consequences.append(event)
    if len(fact_consequences) > MAX_DISCOVERED_FACTS_PER_AI_CHARACTER_TURN:
        raise RuleViolation("proposal_consequence_budget_exceeded", "单个 AICharacter 回合最多发现三个事实")
    fact_ids = [event.fact_id for event in fact_consequences]
    if len(fact_ids) != len(set(fact_ids)):
        raise RuleViolation("duplicate_fact_consequence", "同一回合不能重复发现同一个事实")


def validate_world_state(state: WorldState) -> None:
    if not state.locations:
        raise RuleViolation("world_has_no_locations", "世界必须至少有一个地点")

    actor_ids = [state.player.id, *state.ai_characters, *state.npcs]
    if len(actor_ids) != len(set(actor_ids)):
        raise RuleViolation("actor_id_collision", "Player、AICharacter 与 NPC 的 ID 空间不能重叠")

    def require_location(location_id: str, owner: str) -> None:
        if location_id not in state.locations:
            raise RuleViolation("location_not_found", f"{owner} 指向不存在的地点：{location_id}")

    require_location(state.player.location_id, f"Player {state.player.id}")
    for key, location in state.locations.items():
        if key != location.id:
            raise RuleViolation("location_key_mismatch", f"地点索引 {key} 与实体 ID {location.id} 不一致")
        for target in location.exits:
            require_location(target, f"地点 {location.id} 的出口")
    for key, actor in state.ai_characters.items():
        if key != actor.id:
            raise RuleViolation("actor_key_mismatch", f"AICharacter 索引 {key} 与实体 ID {actor.id} 不一致")
        require_location(actor.location_id, f"AICharacter {actor.id}")
    for key, actor in state.npcs.items():
        if key != actor.id:
            raise RuleViolation("actor_key_mismatch", f"NPC 索引 {key} 与实体 ID {actor.id} 不一致")
        require_location(actor.location_id, f"NPC {actor.id}")

    for key, item in state.items.items():
        if key != item.id:
            raise RuleViolation("item_key_mismatch", f"物品索引 {key} 与实体 ID {item.id} 不一致")
        if item.position.kind == "location":
            require_location(item.position.location_id, f"物品 {item.id}")
        elif item.position.kind == "actor" and not actor_exists(state, item.position.actor):
            raise RuleViolation("item_owner_not_found", f"物品 {item.id} 的所有者不存在")

    for key, relationship in state.relationships.items():
        if key != relationship.id:
            raise RuleViolation("relationship_key_mismatch", f"关系索引 {key} 与实体 ID 不一致")
        if not actor_exists(state, relationship.subject) or not actor_exists(state, relationship.object):
            raise RuleViolation("relationship_actor_not_found", f"关系 {relationship.id} 引用了不存在的角色")
        if relationship.id != relationship_id_for(relationship.subject, relationship.object):
            raise RuleViolation("relationship_id_mismatch", f"关系 {relationship.id} 不是角色对的规范 ID")
    for key, condition in state.conditions.items():
        if key != condition.id:
            raise RuleViolation("condition_key_mismatch", f"状态索引 {key} 与实体 ID 不一致")
        if not actor_exists(state, condition.subject):
            raise RuleViolation("condition_actor_not_found", f"状态 {condition.id} 引用了不存在的角色")
    for key, fact in state.facts.items():
        if key != fact.id:
            raise RuleViolation("fact_key_mismatch", f"事实索引 {key} 与实体 ID 不一致")
        seen: set[tuple[str, str]] = set()
        for actor in fact.known_by:
            key_pair = actor_key(actor)
            if key_pair in seen:
                raise RuleViolation("duplicate_fact_observer", f"事实 {fact.id} 的知情者重复")
            seen.add(key_pair)
            if not actor_exists(state, actor):
                raise RuleViolation("fact_actor_not_found", f"事实 {fact.id} 引用了不存在的知情者")
    for key, thread in state.phone_threads.items():
        if key != thread.id:
            raise RuleViolation("phone_thread_key_mismatch", f"通信线程索引 {key} 与实体 ID 不一致")
        participants = {actor_key(actor) for actor in thread.participants}
        if len(participants) != len(thread.participants):
            raise RuleViolation("duplicate_phone_participant", f"通信线程 {thread.id} 的参与者重复")
        if any(not actor_exists(state, actor) for actor in thread.participants):
            raise RuleViolation("phone_actor_not_found", f"通信线程 {thread.id} 引用了不存在的角色")
        for message in thread.messages:
            if actor_key(message.sender) not in participants:
                raise RuleViolation("phone_sender_not_participant", f"消息 {message.id} 的发送者不在线程参与者中")


def validate_event(state: WorldState, event: WorldEvent) -> None:
    if not actor_exists(state, event.actor):
        raise RuleViolation("actor_not_found", f"行动者不存在：{event.actor.kind}:{event.actor.id}")

    if isinstance(event, MoveActorEvent):
        current = actor_location(state, event.actor)
        if event.from_location_id != current:
            raise RuleViolation("stale_actor_location", "移动事件的起点与当前角色位置不一致")
        if event.to_location_id not in state.locations:
            raise RuleViolation("destination_not_found", f"目标地点不存在：{event.to_location_id}")
        if event.to_location_id not in state.locations[current].exits:
            raise RuleViolation("locations_not_connected", f"{current} 与 {event.to_location_id} 没有可用出口")
        return

    if isinstance(event, TransferItemEvent):
        item = state.items.get(event.item_id)
        if not item:
            raise RuleViolation("item_not_found", f"物品不存在：{event.item_id}")
        if not position_equal(item.position, event.from_position):
            raise RuleViolation("stale_item_position", "物品当前位置与事件来源不一致")
        current_location = actor_location(state, event.actor)
        if isinstance(event.from_position, LocationPosition) and isinstance(event.to_position, ActorPosition):
            if event.from_position.location_id != current_location:
                raise RuleViolation("item_not_present", "只能拾取行动者当前地点的物品")
            if actor_key(event.to_position.actor) != actor_key(event.actor):
                raise RuleViolation("invalid_item_recipient", "拾取物品只能进入行动者自己的库存")
            return
        if isinstance(event.from_position, ActorPosition) and isinstance(event.to_position, LocationPosition):
            if actor_key(event.from_position.actor) != actor_key(event.actor):
                raise RuleViolation("item_not_owned", "只能丢下行动者自己持有的物品")
            if event.to_position.location_id != current_location:
                raise RuleViolation("invalid_drop_location", "物品只能丢在行动者当前地点")
            return
        raise RuleViolation("unsupported_item_transfer", "当前阶段只允许地点与行动者库存之间的物品转移")

    if isinstance(event, AdvanceTimeEvent):
        if event.minutes < 1 or event.minutes > 1440:
            raise RuleViolation("invalid_time_delta", "单次时间推进必须在 1 到 1440 分钟之间")
        return

    if isinstance(event, DiscoverFactEvent):
        fact = state.facts.get(event.fact_id)
        if not fact:
            raise RuleViolation("fact_not_found", f"事实不存在：{event.fact_id}")
        if actor_key(event.actor) in {actor_key(actor) for actor in fact.known_by}:
            raise RuleViolation("fact_already_known", "该角色已经知道这个事实")
        return

    if isinstance(event, AdjustRelationshipEvent):
        if not actor_exists(state, event.subject) or not actor_exists(state, event.object):
            raise RuleViolation("relationship_actor_not_found", "关系事件引用了不存在的角色")
        if actor_key(event.subject) == actor_key(event.object):
            raise RuleViolation("relationship_self_edge", "角色不能和自己建立关系边")
        if event.relationship_id != relationship_id_for(event.subject, event.object):
            raise RuleViolation("relationship_id_mismatch", "关系事件必须使用服务器定义的规范 ID")
        if actor_key(event.actor) not in {actor_key(event.subject), actor_key(event.object)}:
            raise RuleViolation("relationship_actor_not_involved", "行动者必须是关系变化的一方")
        if actor_location(state, event.subject) != actor_location(state, event.object):
            raise RuleViolation("relationship_actors_not_together", "当前阶段只有同一地点的角色才能改变关系")
        deltas = (
            event.affinity_delta,
            event.trust_delta,
            event.fear_delta,
            event.respect_delta,
        )
        if not any(deltas):
            raise RuleViolation("empty_relationship_delta", "关系事件至少要改变一个维度")
        existing = state.relationships.get(event.relationship_id)
        if existing:
            if actor_key(existing.subject) != actor_key(event.subject) or actor_key(existing.object) != actor_key(event.object):
                raise RuleViolation("relationship_identity_mismatch", "关系 ID 已绑定到其他角色方向")
            values = (
                existing.affinity + event.affinity_delta,
                existing.trust + event.trust_delta,
                existing.fear + event.fear_delta,
                existing.respect + event.respect_delta,
            )
        else:
            values = deltas
        affinity, trust, fear, respect = values
        if not -100 <= affinity <= 100 or not -100 <= trust <= 100:
            raise RuleViolation("relationship_out_of_range", "关系变化超出 -100 到 100 的范围")
        if not 0 <= fear <= 100:
            raise RuleViolation("relationship_out_of_range", "恐惧值变化超出 0 到 100 的范围")
        if not -100 <= respect <= 100:
            raise RuleViolation("relationship_out_of_range", "尊重值变化超出 -100 到 100 的范围")
        return

    if isinstance(event, AdvanceQuestEvent):
        quest = state.quests.get(event.quest_id)
        if not quest:
            raise RuleViolation("quest_not_found", f"任务不存在：{event.quest_id}")
        if quest.status != event.from_status:
            raise RuleViolation("stale_quest_status", "任务当前状态与事件起点不一致")
        allowed = {
            "hidden": {"available", "active"},
            "available": {"active"},
            "active": {"completed", "failed"},
            "completed": set(),
            "failed": set(),
        }
        if event.to_status not in allowed[event.from_status]:
            raise RuleViolation("invalid_quest_transition", f"任务不能从 {event.from_status} 变为 {event.to_status}")
        return

    raise RuleViolation("unsupported_event", "不支持的领域事件")
