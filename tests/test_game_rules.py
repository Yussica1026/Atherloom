import unittest

from pydantic import ValidationError

from backend.game.engine import player_action_events, state_digest, validate_and_apply_events
from backend.game.events import (
    AdvanceQuestEvent,
    AdvanceTimeEvent,
    AdjustRelationshipEvent,
    DiscoverFactEvent,
    MoveActorEvent,
    parse_world_event,
    relationship_id_for,
)
from backend.game.models import (
    AICharacterActionIn,
    FactState,
    ItemState,
    LocationPosition,
    LocationState,
    NPCState,
    NPCRef,
    PlayerActionIn,
    PlayerRef,
    PlayerState,
    QuestState,
    WorldState,
)
from backend.game.rules import RuleViolation, validate_world_state


def seed_state() -> WorldState:
    player = PlayerRef(id="player-1")
    return WorldState(
        world_id="world-1",
        world_version_id="worldv-1",
        session_id="session-1",
        player=PlayerState(id=player.id, display_name="测试玩家", location_id="home"),
        locations={
            "home": LocationState(id="home", name="家", exits=["station"], visited=True),
            "station": LocationState(id="station", name="车站", exits=["home"]),
            "garden": LocationState(id="garden", name="花园", exits=[]),
        },
        items={
            "ticket": ItemState(
                id="ticket",
                name="旧车票",
                position=LocationPosition(location_id="home"),
            )
        },
        npcs={
            "keeper": NPCState(
                id="keeper",
                name="守站人",
                location_id="station",
            )
        },
        facts={
            "last-train": FactState(id="last-train", text="末班车从未真正离站")
        },
    )


class GameSchemaTests(unittest.TestCase):
    def test_player_action_schema_is_strict_and_forbids_extra_fields(self):
        valid = {
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "move", "destination_id": "station"},
            "expected_revision": 0,
            "idempotency_key": "action-1",
        }
        action = PlayerActionIn.model_validate(valid)
        self.assertEqual(action.intent.type, "move")
        with self.assertRaises(ValidationError):
            PlayerActionIn.model_validate({**valid, "expected_revision": "0"})
        with self.assertRaises(ValidationError):
            PlayerActionIn.model_validate({**valid, "unexpected": True})
        with self.assertRaises(ValidationError):
            PlayerActionIn.model_validate({**valid, "intent": {"type": "teleport", "destination_id": "station"}})

    def test_event_union_is_discriminated_and_fail_closed(self):
        event = parse_world_event({
            "type": "move_actor",
            "event_id": "event-1",
            "actor": {"kind": "player", "id": "player-1"},
            "from_location_id": "home",
            "to_location_id": "station",
        })
        self.assertIsInstance(event, MoveActorEvent)
        with self.assertRaises(ValidationError):
            parse_world_event({"type": "replace_state", "event_id": "event-2", "state": {}})
        with self.assertRaises(ValidationError):
            parse_world_event({
                "type": "move_actor",
                "event_id": "event-3",
                "actor": {"kind": "player", "id": "player-1"},
                "from_location_id": "home",
                "to_location_id": "station",
                "database_patch": "DROP TABLE",
            })

    def test_freeform_is_player_only_and_requires_non_blank_content(self):
        base = {
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "freeform"},
            "expected_revision": 0,
            "idempotency_key": "freeform-1",
        }
        action = PlayerActionIn.model_validate({
            **base,
            "content": "  我敲了敲售票窗，问还有没有末班车。  ",
        })
        self.assertEqual(action.intent.type, "freeform")
        self.assertTrue(action.content.startswith("  "))
        for content in ("", "   ", "\n\t"):
            with self.subTest(content=content), self.assertRaises(ValidationError):
                PlayerActionIn.model_validate({**base, "content": content})

        with self.assertRaises(ValidationError):
            AICharacterActionIn.model_validate({
                "actor": {"kind": "ai_character", "id": "resident-1"},
                "intent": {"type": "freeform"},
                "content": "自行发挥",
                "expected_revision": 0,
                "idempotency_key": "ai-freeform",
            })


class GameRuleTests(unittest.TestCase):
    def test_direct_rule_translation_explicitly_rejects_freeform(self):
        state = seed_state()
        action = PlayerActionIn.model_validate({
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "freeform"},
            "content": "我尝试和雾里的声音交谈。",
            "expected_revision": 0,
            "idempotency_key": "freeform-needs-gm",
        })
        with self.assertRaises(RuleViolation) as rejected:
            player_action_events(state, action)
        self.assertEqual(rejected.exception.code, "freeform_requires_game_master")

    def test_valid_move_updates_clone_once_and_keeps_source_unchanged(self):
        state = seed_state()
        action = PlayerActionIn.model_validate({
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "move", "destination_id": "station"},
            "expected_revision": 0,
            "idempotency_key": "move-1",
        })
        events = player_action_events(state, action, lambda: "event-move")
        committed = validate_and_apply_events(state, events, 1)
        self.assertEqual(state.player.location_id, "home")
        self.assertEqual(state.revision, 0)
        self.assertEqual(committed.player.location_id, "station")
        self.assertTrue(committed.locations["station"].visited)
        self.assertEqual(committed.revision, 1)
        self.assertEqual(committed.turn, 1)

    def test_invalid_second_event_rolls_back_entire_batch(self):
        state = seed_state()
        before_hash = state_digest(state)
        events = [
            AdvanceTimeEvent(event_id="event-time", actor=PlayerRef(id="player-1"), minutes=15),
            MoveActorEvent(
                event_id="event-bad-move",
                actor=PlayerRef(id="player-1"),
                from_location_id="home",
                to_location_id="garden",
            ),
        ]
        with self.assertRaisesRegex(RuleViolation, "没有可用出口"):
            validate_and_apply_events(state, events, 1)
        self.assertEqual(state_digest(state), before_hash)
        self.assertEqual(state.clock.total_minutes, 0)
        self.assertEqual(state.revision, 0)

    def test_take_and_drop_use_one_canonical_item_position(self):
        state = seed_state()
        take = PlayerActionIn.model_validate({
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "take_item", "item_id": "ticket"},
            "expected_revision": 0,
            "idempotency_key": "take-1",
        })
        held = validate_and_apply_events(state, player_action_events(state, take, lambda: "event-take"), 1)
        self.assertEqual(held.items["ticket"].position.kind, "actor")
        self.assertEqual(held.items["ticket"].position.actor.id, "player-1")
        drop = PlayerActionIn.model_validate({
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "drop_item", "item_id": "ticket"},
            "expected_revision": 1,
            "idempotency_key": "drop-1",
        })
        dropped = validate_and_apply_events(held, player_action_events(held, drop, lambda: "event-drop"), 2)
        self.assertEqual(dropped.items["ticket"].position, LocationPosition(location_id="home"))

    def test_fact_discovery_is_typed_and_cannot_be_repeated(self):
        state = seed_state()
        event = DiscoverFactEvent(
            event_id="event-fact",
            actor=PlayerRef(id="player-1"),
            fact_id="last-train",
        )
        discovered = validate_and_apply_events(state, [event], 1)
        self.assertEqual(discovered.facts["last-train"].known_by, [PlayerRef(id="player-1")])
        with self.assertRaisesRegex(RuleViolation, "已经知道"):
            validate_and_apply_events(discovered, [event.model_copy(update={"event_id": "event-fact-2"})], 2)

    def test_actor_id_spaces_cannot_collide(self):
        state = seed_state()
        state.npcs["player-1"] = NPCState(
            id="player-1",
            name="伪装者",
            location_id="home",
        )
        with self.assertRaisesRegex(RuleViolation, "ID 空间不能重叠"):
            validate_world_state(state)

    def test_relationship_change_requires_both_actors_to_share_a_location(self):
        state = seed_state()
        event = AdjustRelationshipEvent(
            event_id="event-help",
            actor=PlayerRef(id="player-1"),
            relationship_id=relationship_id_for(
                PlayerRef(id="player-1"),
                NPCRef(id="keeper"),
            ),
            subject=PlayerRef(id="player-1"),
            object={"kind": "npc", "id": "keeper"},
            affinity_delta=5,
            trust_delta=3,
            respect_delta=2,
            cause="help",
        )
        with self.assertRaises(RuleViolation) as rejected:
            validate_and_apply_events(state, [event], 1)
        self.assertEqual(rejected.exception.code, "relationship_actors_not_together")
        self.assertEqual(state.relationships, {})

        move = MoveActorEvent(
            event_id="event-move-to-keeper",
            actor=PlayerRef(id="player-1"),
            from_location_id="home",
            to_location_id="station",
        )
        colocated = validate_and_apply_events(state, [move], 1)
        updated = validate_and_apply_events(colocated, [event], 2)
        relationship = updated.relationships[event.relationship_id]
        self.assertEqual(
            (relationship.affinity, relationship.trust, relationship.fear, relationship.respect),
            (5, 3, 0, 2),
        )

    def test_quest_transition_is_validated_before_commit(self):
        state = seed_state()
        state.quests["find-platform"] = QuestState(
            id="find-platform",
            title="Find the old platform",
            status="available",
        )
        invalid = AdvanceQuestEvent(
            event_id="event-skip-quest",
            actor=PlayerRef(id="player-1"),
            quest_id="find-platform",
            from_status="available",
            to_status="completed",
        )
        with self.assertRaises(RuleViolation) as rejected:
            validate_and_apply_events(state, [invalid], 1)
        self.assertEqual(rejected.exception.code, "invalid_quest_transition")
        self.assertEqual(state.quests["find-platform"].status, "available")

        accepted = AdvanceQuestEvent(
            event_id="event-accept-quest",
            actor=PlayerRef(id="player-1"),
            quest_id="find-platform",
            from_status="available",
            to_status="active",
        )
        committed = validate_and_apply_events(state, [accepted], 1)
        self.assertEqual(committed.quests["find-platform"].status, "active")


if __name__ == "__main__":
    unittest.main()
