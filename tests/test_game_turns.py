import asyncio
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError

from backend.game.engine import canonical_json
from backend.game.events import (
    AdvanceQuestEvent,
    AdvanceTimeEvent,
    AdjustRelationshipEvent,
    DiscoverFactEvent,
    MoveActorEvent,
    TransferItemEvent,
    relationship_id_for,
)
from backend.game.models import (
    NPCRef,
    PlayerActionIn,
    PlayerRef,
    RemovedPosition,
    SessionCreateIn,
    WorldCreateIn,
    WaitIntent,
)
from backend.game.proposals import (
    MAX_PROPOSED_EVENTS,
    ProposedAdjustRelationshipEvent,
    ProposedAdvanceQuestEvent,
    ProposedAdvanceTimeEvent,
    ProposedDiscoverFactEvent,
    ProposedMoveActorEvent,
    ProposedTransferItemEvent,
    TurnProposal,
    materialize_turn_proposal,
    parse_proposed_world_event,
)
from backend.game.repository import GameConflict, GameDataError, GameRepository
from backend.game.rules import RuleViolation
from backend.game.schema import connect
from backend.game.service import LongWorldTurnService


WORLD_INPUT = {
    "name": "雾港",
    "description": "一座会记住选择的沿海城镇。",
    "starting_location_id": "home",
    "locations": [
        {"id": "home", "name": "阁楼", "exits": ["station"]},
        {"id": "station", "name": "旧车站", "exits": ["home"]},
        {"id": "garden", "name": "封闭花园", "exits": []},
    ],
    "items": [
        {"id": "ticket", "name": "旧车票", "initial_location_id": "home"},
    ],
    "npcs": [
        {"id": "keeper", "name": "守站人", "initial_location_id": "station"},
    ],
    "facts": [
        {"id": "last-train", "text": "末班车从未真正离站"},
    ],
    "quests": [
        {"id": "find-platform", "title": "找到旧站台"},
    ],
}


PLAYER = {"kind": "player", "id": "player-1"}


def proposal_payload(context, events, narration=None):
    return {
        "schema_version": 1,
        "session_id": context.session_id,
        "based_on_revision": context.based_on_revision,
        "based_on_state_hash": context.based_on_state_hash,
        "initiator": PLAYER,
        "events": events,
        "narration": narration or {"text": "你沿着雾里的石阶走向旧车站。"},
    }


def move_event(actor=PLAYER):
    return {
        "type": "move_actor",
        "actor": actor,
        "from_location_id": "home",
        "to_location_id": "station",
    }


def arrival_relationship_event():
    return {
        "type": "adjust_relationship",
        "actor": PLAYER,
        "subject": PLAYER,
        "object": {"kind": "npc", "id": "keeper"},
        "affinity_delta": 1,
        "trust_delta": 1,
        "cause": "conversation",
    }


class FakeGameMaster:
    def __init__(self, factory):
        self.factory = factory
        self.calls = 0
        self.contexts = []

    async def propose_turn(self, context):
        self.calls += 1
        self.contexts.append(context)
        return self.factory(context)


class TurnProposalSchemaTests(unittest.TestCase):
    def proposal(self, events):
        return {
            "schema_version": 1,
            "session_id": "session-1",
            "based_on_revision": 0,
            "based_on_state_hash": "0" * 64,
            "initiator": PLAYER,
            "events": events,
            "narration": {"text": "雾声在站台边缓慢散开。"},
        }

    def test_all_candidate_event_shapes_parse_without_authoritative_ids(self):
        candidates = [
            move_event(),
            {
                "type": "transfer_item",
                "actor": PLAYER,
                "item_id": "ticket",
                "from_position": {"kind": "location", "location_id": "home"},
                "to_position": {"kind": "actor", "actor": PLAYER},
            },
            {"type": "advance_time", "actor": PLAYER, "minutes": 10},
            {"type": "discover_fact", "actor": PLAYER, "fact_id": "last-train"},
            arrival_relationship_event(),
            {
                "type": "advance_quest",
                "actor": PLAYER,
                "quest_id": "find-platform",
                "from_status": "available",
                "to_status": "active",
            },
        ]
        parsed = TurnProposal.model_validate(self.proposal(candidates))
        self.assertEqual([event.type for event in parsed.events], [item["type"] for item in candidates])
        self.assertTrue(all("event_id" not in event.model_dump(mode="json") for event in parsed.events))

    def test_candidate_schema_fails_closed_on_commit_fields_unknown_types_and_batch_limits(self):
        with self.assertRaises(ValidationError):
            parse_proposed_world_event({**move_event(), "event_id": "model-owned-id"})
        with self.assertRaises(ValidationError):
            parse_proposed_world_event({**move_event(), "database_patch": "DROP TABLE"})
        with self.assertRaises(ValidationError):
            parse_proposed_world_event({
                **arrival_relationship_event(),
                "relationship_id": "rel:model-controlled",
            })
        with self.assertRaises(ValidationError):
            parse_proposed_world_event({"type": "replace_state", "state": {}})
        with self.assertRaises(ValidationError):
            TurnProposal.model_validate(self.proposal([]))
        with self.assertRaises(ValidationError):
            TurnProposal.model_validate(self.proposal([move_event()] * (MAX_PROPOSED_EVENTS + 1)))
        with self.assertRaises(ValidationError):
            TurnProposal.model_validate({**self.proposal([move_event()]), "state": {}})

    def test_narration_is_trimmed_and_rejects_empty_extra_or_non_string_values(self):
        parsed = TurnProposal.model_validate({
            **self.proposal([move_event()]),
            "narration": {"text": "  雾声抵达站台。  "},
        })
        self.assertEqual(parsed.narration.text, "雾声抵达站台。")

        invalid_narrations = (
            {"text": "   "},
            {"text": "有效文字", "database_patch": "UPDATE state"},
            {"text": 7},
        )
        for narration in invalid_narrations:
            with self.subTest(narration=narration), self.assertRaises(ValidationError):
                TurnProposal.model_validate({
                    **self.proposal([move_event()]),
                    "narration": narration,
                })

    def test_candidate_and_committed_event_schemas_cannot_silently_drift(self):
        pairs = [
            (MoveActorEvent, ProposedMoveActorEvent, {"event_id"}),
            (TransferItemEvent, ProposedTransferItemEvent, {"event_id"}),
            (AdvanceTimeEvent, ProposedAdvanceTimeEvent, {"event_id"}),
            (DiscoverFactEvent, ProposedDiscoverFactEvent, {"event_id"}),
            (
                AdjustRelationshipEvent,
                ProposedAdjustRelationshipEvent,
                {"event_id", "relationship_id"},
            ),
            (AdvanceQuestEvent, ProposedAdvanceQuestEvent, {"event_id"}),
        ]
        for committed, proposed, server_owned in pairs:
            with self.subTest(event=committed.__name__):
                self.assertEqual(
                    set(committed.model_fields) - server_owned,
                    set(proposed.model_fields),
                )

    def test_materialization_assigns_unique_server_owned_event_ids(self):
        proposal = TurnProposal.model_validate(self.proposal([move_event()]))
        committed = materialize_turn_proposal(proposal, lambda: "event-server-1")
        self.assertEqual(committed[0].event_id, "event-server-1")
        self.assertNotIn("event_id", proposal.events[0].model_dump(mode="json"))

        duplicate = TurnProposal.model_validate(
            self.proposal([move_event(), arrival_relationship_event()])
        )
        with self.assertRaises(RuleViolation) as rejected:
            materialize_turn_proposal(duplicate, lambda: "event-duplicate")
        self.assertEqual(rejected.exception.code, "duplicate_event_id")

    def test_relationship_identity_is_unambiguous_even_when_actor_ids_contain_colons(self):
        first = relationship_id_for(NPCRef(id="a:npc:b"), NPCRef(id="c"))
        second = relationship_id_for(NPCRef(id="a"), NPCRef(id="b:npc:c"))
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 120)
        self.assertLessEqual(len(second), 120)


class LongWorldTurnServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "turns.db"
        self.repository = GameRepository(self.db_path)
        self.repository.init()
        world = self.repository.create_world(WorldCreateIn.model_validate(WORLD_INPUT))
        self.session = self.repository.create_session(SessionCreateIn(
            world_id=world["id"],
            player_id="player-1",
            player_display_name="测试玩家",
            branch_name="主线",
        ))

    def tearDown(self):
        self.tempdir.cleanup()

    def action(self, *, revision=0, key="gm-move", intent=None, content="去旧车站"):
        return PlayerActionIn.model_validate({
            "actor": PLAYER,
            "intent": intent or {"type": "move", "destination_id": "station"},
            "content": content,
            "expected_revision": revision,
            "idempotency_key": key,
        })

    def run_turn(self, game_master, action=None):
        service = LongWorldTurnService(self.repository, game_master)
        return asyncio.run(service.run_player_turn(self.session["id"], action or self.action()))

    def table_counts(self):
        with closing(connect(self.db_path)) as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "world_game_turns",
                    "world_game_turn_outputs",
                    "world_game_events",
                    "world_game_snapshots",
                )
            }

    def successful_factory(self, context):
        return proposal_payload(context, [
            move_event(),
            {"type": "discover_fact", "actor": PLAYER, "fact_id": "last-train"},
        ])

    def freeform_action(self, *, key="freeform-turn", content="我拿起车票，走到站台询问末班车。"):
        return self.action(
            key=key,
            intent={"type": "freeform"},
            content=content,
        )

    def freeform_factory(self, context):
        return proposal_payload(context, [
            {
                "type": "transfer_item",
                "actor": PLAYER,
                "item_id": "ticket",
                "from_position": {"kind": "location", "location_id": "home"},
                "to_position": {"kind": "actor", "actor": PLAYER},
            },
            move_event(),
            arrival_relationship_event(),
            {"type": "discover_fact", "actor": PLAYER, "fact_id": "last-train"},
            {
                "type": "advance_quest",
                "actor": PLAYER,
                "quest_id": "find-platform",
                "from_status": "available",
                "to_status": "active",
            },
            {"type": "advance_time", "actor": PLAYER, "minutes": 5},
        ], {"text": "你收好旧车票，在雾中抵达站台并向守站人问起末班车。"})

    def test_fake_gm_commits_ordered_multi_event_turn_and_replays_exactly(self):
        gm = FakeGameMaster(self.successful_factory)
        committed = self.run_turn(gm)

        self.assertEqual(gm.calls, 1)
        self.assertEqual(committed["committed_revision"], 1)
        self.assertEqual([event["type"] for event in committed["events"]], [
            "move_actor",
            "discover_fact",
        ])
        self.assertTrue(all(event["event_id"].startswith("event-") for event in committed["events"]))
        self.assertEqual(committed["state"]["turn"], 1)
        self.assertEqual(
            committed["narration"],
            {"text": "你沿着雾里的石阶走向旧车站。"},
        )
        self.assertEqual(committed["state"]["player"]["location_id"], "station")
        self.assertEqual(
            committed["state"]["facts"]["last-train"]["known_by"],
            [PLAYER],
        )

        rows = self.repository.list_events(self.session["id"])
        self.assertEqual([row["sequence"] for row in rows], [0, 1])
        turns = self.repository.list_turns(self.session["id"])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["narration"], committed["narration"])
        self.assertEqual(turns[0]["events"], committed["events"])
        replay = self.repository.replay_result(self.session["id"])
        self.assertEqual(canonical_json(replay["state"]), canonical_json(committed["state"]))
        self.assertEqual(replay["state_hash"], committed["state_hash"])
        self.assertTrue(replay["matches_current"])

    def test_freeform_gm_commits_six_valid_events_atomically_and_retries_idempotently(self):
        game_master = FakeGameMaster(self.freeform_factory)
        action = self.freeform_action()
        committed = self.run_turn(game_master, action)

        self.assertEqual(game_master.calls, 1)
        self.assertEqual(len(committed["events"]), 6)
        self.assertEqual(committed["committed_revision"], 1)
        self.assertEqual(committed["state"]["player"]["location_id"], "station")
        self.assertEqual(
            committed["state"]["items"]["ticket"]["position"],
            {"kind": "actor", "actor": PLAYER},
        )
        self.assertEqual(committed["state"]["clock"]["total_minutes"], 5)
        self.assertEqual(committed["state"]["quests"]["find-platform"]["status"], "active")
        self.assertEqual(committed["state"]["facts"]["last-train"]["known_by"], [PLAYER])
        self.assertEqual(
            committed["narration"],
            {"text": "你收好旧车票，在雾中抵达站台并向守站人问起末班车。"},
        )
        self.assertTrue(self.repository.replay_result(self.session["id"])["matches_current"])

        retried = self.run_turn(game_master, action)
        self.assertTrue(retried["idempotent_replay"])
        self.assertEqual(retried["events"], committed["events"])
        self.assertEqual(retried["narration"], committed["narration"])
        self.assertEqual(game_master.calls, 1)

    def test_freeform_rejects_foreign_actor_invalid_fact_and_event_over_budget_without_writes(self):
        cases = (
            (
                "foreign-actor",
                [move_event({"kind": "npc", "id": "keeper"})],
                "proposal_actor_not_authorized",
            ),
            (
                "missing-fact",
                [{"type": "discover_fact", "actor": PLAYER, "fact_id": "missing-fact"}],
                "fact_not_found",
            ),
            (
                "over-budget",
                [
                    {"type": "advance_time", "actor": PLAYER, "minutes": 1}
                    for _ in range(7)
                ],
                "freeform_event_budget_exceeded",
            ),
        )
        for key, events, expected_code in cases:
            game_master = FakeGameMaster(
                lambda context, values=events: proposal_payload(context, values)
            )
            with self.subTest(case=key), self.assertRaises(RuleViolation) as rejected:
                self.run_turn(game_master, self.freeform_action(key=f"freeform-{key}"))
            self.assertEqual(rejected.exception.code, expected_code)
            self.assertEqual(game_master.calls, 1)

        self.assertEqual(self.table_counts(), {
            "world_game_turns": 0,
            "world_game_turn_outputs": 0,
            "world_game_events": 0,
            "world_game_snapshots": 1,
        })

    def test_invalid_second_ordered_event_rolls_back_the_entire_repository_batch(self):
        before = self.repository.get_session(self.session["id"])
        gm = FakeGameMaster(
            lambda context: proposal_payload(
                context,
                [
                    move_event(),
                    {"type": "discover_fact", "actor": PLAYER, "fact_id": "missing-fact"},
                ],
            )
        )
        with self.assertRaises(RuleViolation) as rejected:
            self.run_turn(gm)
        self.assertEqual(rejected.exception.code, "fact_not_found")

        after = self.repository.get_session(self.session["id"])
        self.assertEqual(after["current_revision"], 0)
        self.assertEqual(after["state_hash"], before["state_hash"])
        self.assertEqual(canonical_json(after["state"]), canonical_json(before["state"]))
        self.assertEqual(self.table_counts(), {
            "world_game_turns": 0,
            "world_game_turn_outputs": 0,
            "world_game_events": 0,
            "world_game_snapshots": 1,
        })

    def test_invalid_narration_shapes_never_write_a_partial_turn(self):
        invalid_narrations = (
            {"text": "   "},
            {"text": "看似有效", "state_patch": {"revision": 99}},
            {"text": ["不是字符串"]},
        )
        for index, narration in enumerate(invalid_narrations):
            game_master = FakeGameMaster(
                lambda context, value=narration: proposal_payload(
                    context,
                    [move_event()],
                    value,
                )
            )
            with self.subTest(narration=narration), self.assertRaises(ValidationError):
                self.run_turn(
                    game_master,
                    self.action(key=f"invalid-narration-{index}"),
                )
            self.assertEqual(game_master.calls, 1)

        self.assertEqual(self.table_counts(), {
            "world_game_turns": 0,
            "world_game_turn_outputs": 0,
            "world_game_events": 0,
            "world_game_snapshots": 1,
        })

    def test_player_proposal_cannot_move_an_npc_or_supply_commit_metadata(self):
        npc_move = move_event({"kind": "npc", "id": "keeper"})
        gm = FakeGameMaster(lambda context: proposal_payload(context, [npc_move]))
        with self.assertRaises(RuleViolation) as rejected:
            self.run_turn(gm)
        self.assertEqual(rejected.exception.code, "proposal_actor_not_authorized")
        self.assertEqual(self.table_counts()["world_game_turns"], 0)

        gm_with_id = FakeGameMaster(
            lambda context: proposal_payload(context, [{**move_event(), "event_id": "from-model"}])
        )
        with self.assertRaises(ValidationError):
            self.run_turn(gm_with_id, self.action(key="gm-with-id"))
        self.assertEqual(self.table_counts()["world_game_events"], 0)

    def test_relationship_identity_and_help_budget_are_server_controlled(self):
        self.repository.commit_player_action(self.session["id"], self.action(key="setup-move"))
        help_action = self.action(
            revision=1,
            key="help-keeper",
            intent={"type": "help_actor", "target": {"kind": "npc", "id": "keeper"}},
            content="帮守站人整理散落的车票",
        )

        def help_factory(context):
            event = {
                **arrival_relationship_event(),
                "affinity_delta": 5,
                "trust_delta": 3,
                "respect_delta": 2,
                "cause": "help",
            }
            return proposal_payload(context, [event])

        committed = self.run_turn(FakeGameMaster(help_factory), help_action)
        relationship_id = relationship_id_for(
            PlayerRef(id="player-1"),
            NPCRef(id="keeper"),
        )
        self.assertEqual(committed["events"][0]["relationship_id"], relationship_id)
        self.assertIn(relationship_id, committed["state"]["relationships"])

        harmful_help = self.action(
            revision=2,
            key="harmful-help",
            intent={"type": "help_actor", "target": {"kind": "npc", "id": "keeper"}},
        )
        harmful_gm = FakeGameMaster(lambda context: proposal_payload(context, [{
            **arrival_relationship_event(),
            "affinity_delta": -10,
            "fear_delta": 10,
            "cause": "help",
        }]))
        with self.assertRaises(RuleViolation) as rejected:
            self.run_turn(harmful_gm, harmful_help)
        self.assertEqual(rejected.exception.code, "proposal_help_delta_out_of_policy")
        self.assertEqual(self.repository.get_session(self.session["id"])["current_revision"], 2)

    def test_relation_consequences_and_oversized_fact_reveals_are_not_player_authorized(self):
        relationship_gm = FakeGameMaster(
            lambda context: proposal_payload(context, [move_event(), arrival_relationship_event()])
        )
        with self.assertRaises(RuleViolation) as rejected:
            self.run_turn(relationship_gm)
        self.assertEqual(rejected.exception.code, "proposal_event_not_authorized")

        facts = [
            {"type": "discover_fact", "actor": PLAYER, "fact_id": "last-train"}
            for _ in range(4)
        ]
        fact_gm = FakeGameMaster(lambda context: proposal_payload(context, [move_event(), *facts]))
        with self.assertRaises(RuleViolation) as rejected:
            self.run_turn(fact_gm, self.action(key="too-many-facts"))
        self.assertEqual(rejected.exception.code, "proposal_consequence_budget_exceeded")
        self.assertEqual(self.table_counts()["world_game_turns"], 0)

    def test_stale_proposal_loses_to_a_concurrent_commit_without_holding_a_write_lock(self):
        def race_factory(context):
            concurrent = self.action(
                key="concurrent-wait",
                intent={"type": "wait", "minutes": 5},
                content="先等五分钟",
            )
            self.repository.commit_player_action(self.session["id"], concurrent)
            return proposal_payload(context, [move_event()])

        gm = FakeGameMaster(race_factory)
        with self.assertRaises(GameConflict):
            self.run_turn(gm)
        current = self.repository.get_session(self.session["id"])
        self.assertEqual(current["current_revision"], 1)
        self.assertEqual(current["state"]["clock"]["total_minutes"], 5)
        self.assertEqual(current["state"]["player"]["location_id"], "home")
        self.assertEqual(self.table_counts()["world_game_turns"], 1)

    def test_late_idempotent_retry_returns_first_snapshot_without_calling_gm_again(self):
        gm = FakeGameMaster(self.successful_factory)
        action = self.action()
        first = self.run_turn(gm, action)
        self.repository.commit_player_action(
            self.session["id"],
            self.action(
                revision=1,
                key="later-wait",
                intent={"type": "wait", "minutes": 7},
                content="后来又等了七分钟",
            ),
        )

        retried = self.run_turn(gm, action)
        self.assertEqual(gm.calls, 1)
        self.assertTrue(retried["idempotent_replay"])
        self.assertEqual(retried["action_id"], first["action_id"])
        self.assertEqual(retried["events"], first["events"])
        self.assertEqual(retried["narration"], first["narration"])
        self.assertEqual(retried["state_hash"], first["state_hash"])
        self.assertEqual(retried["state"], first["state"])
        self.assertEqual(self.repository.get_session(self.session["id"])["current_revision"], 2)

    def test_provider_context_mutation_cannot_mutate_authoritative_state(self):
        def mutating_factory(context):
            context.state.items["ticket"].position = RemovedPosition()
            return proposal_payload(context, [move_event()])

        committed = self.run_turn(FakeGameMaster(mutating_factory))
        self.assertEqual(
            committed["state"]["items"]["ticket"]["position"],
            {"kind": "location", "location_id": "home"},
        )

    def test_original_action_mutation_during_gm_call_cannot_change_the_trusted_intent(self):
        action = self.action(key="mutated-caller-action")

        def mutating_factory(context):
            action.intent = WaitIntent(minutes=9)
            return proposal_payload(context, [move_event()])

        committed = self.run_turn(FakeGameMaster(mutating_factory), action)
        self.assertEqual(committed["state"]["player"]["location_id"], "station")
        self.assertEqual(committed["state"]["clock"]["total_minutes"], 0)
        with closing(connect(self.db_path)) as connection:
            stored_intent = json.loads(connection.execute(
                "SELECT intent_json FROM world_game_turns WHERE session_id=?",
                (self.session["id"],),
            ).fetchone()["intent_json"])
        self.assertEqual(stored_intent, {"type": "move", "destination_id": "station"})

    def test_second_event_sql_failure_rolls_back_and_same_key_can_retry(self):
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER fail_second_proposed_event
                BEFORE INSERT ON world_game_events
                WHEN NEW.sequence=1
                BEGIN
                  SELECT RAISE(FAIL, 'simulated second event failure');
                END
                """
            )
            connection.commit()

        gm = FakeGameMaster(self.successful_factory)
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_turn(gm)
        self.assertEqual(self.table_counts(), {
            "world_game_turns": 0,
            "world_game_turn_outputs": 0,
            "world_game_events": 0,
            "world_game_snapshots": 1,
        })
        current = self.repository.get_session(self.session["id"])
        self.assertEqual(current["current_revision"], 0)
        self.assertEqual(current["state"]["player"]["location_id"], "home")

        with closing(connect(self.db_path)) as connection:
            connection.execute("DROP TRIGGER fail_second_proposed_event")
            connection.commit()
        committed = self.run_turn(gm)
        self.assertEqual(gm.calls, 2)
        self.assertEqual(committed["committed_revision"], 1)
        self.assertEqual(self.table_counts()["world_game_events"], 2)

    def test_output_sql_failure_rolls_back_events_state_and_snapshot_atomically(self):
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER fail_turn_output
                BEFORE INSERT ON world_game_turn_outputs
                BEGIN
                  SELECT RAISE(FAIL, 'simulated output failure');
                END
                """
            )
            connection.commit()

        game_master = FakeGameMaster(self.successful_factory)
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_turn(game_master)
        self.assertEqual(self.table_counts(), {
            "world_game_turns": 0,
            "world_game_turn_outputs": 0,
            "world_game_events": 0,
            "world_game_snapshots": 1,
        })
        current = self.repository.get_session(self.session["id"])
        self.assertEqual(current["current_revision"], 0)
        self.assertEqual(current["state"]["player"]["location_id"], "home")

        with closing(connect(self.db_path)) as connection:
            connection.execute("DROP TRIGGER fail_turn_output")
            connection.commit()
        committed = self.run_turn(game_master)
        self.assertEqual(game_master.calls, 2)
        self.assertEqual(committed["committed_revision"], 1)
        self.assertEqual(self.table_counts()["world_game_turn_outputs"], 1)

    def test_tampered_narration_is_detected_but_fact_replay_ignores_prose(self):
        game_master = FakeGameMaster(self.successful_factory)
        action = self.action(key="tampered-narration")
        committed = self.run_turn(game_master, action)
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT turn_id,output_json FROM world_game_turn_outputs"
            ).fetchone()
            output = json.loads(row["output_json"])
            output["text"] = "被篡改的叙事"
            connection.execute(
                "UPDATE world_game_turn_outputs SET output_json=? WHERE turn_id=?",
                (canonical_json(output), row["turn_id"]),
            )
            connection.commit()

        replayed = self.repository.replay_result(self.session["id"])
        self.assertTrue(replayed["matches_current"])
        self.assertEqual(replayed["state_hash"], committed["state_hash"])
        with self.assertRaises(GameDataError):
            self.repository.list_turns(self.session["id"])
        with self.assertRaises(GameDataError):
            self.run_turn(game_master, action)
        self.assertEqual(game_master.calls, 1)

    def test_player_identity_mismatch_is_rejected_before_gm_invocation(self):
        gm = FakeGameMaster(self.successful_factory)
        impostor = PlayerActionIn.model_validate({
            "actor": {"kind": "player", "id": "impostor"},
            "intent": {"type": "move", "destination_id": "station"},
            "expected_revision": 0,
            "idempotency_key": "impostor-move",
        })
        with self.assertRaises(RuleViolation) as rejected:
            self.run_turn(gm, impostor)
        self.assertEqual(rejected.exception.code, "player_mismatch")
        self.assertEqual(gm.calls, 0)
        self.assertEqual(self.table_counts()["world_game_turns"], 0)

    def test_hidden_quest_cannot_be_accepted_through_a_player_command(self):
        hidden_world = copy.deepcopy(WORLD_INPUT)
        hidden_world["quests"][0]["initial_status"] = "hidden"
        world = self.repository.create_world(WorldCreateIn.model_validate(hidden_world))
        hidden_session = self.repository.create_session(SessionCreateIn(
            world_id=world["id"],
            player_id="hidden-player",
            player_display_name="隐线玩家",
        ))
        action = PlayerActionIn.model_validate({
            "actor": {"kind": "player", "id": "hidden-player"},
            "intent": {"type": "accept_quest", "quest_id": "find-platform"},
            "expected_revision": 0,
            "idempotency_key": "accept-hidden",
        })
        with self.assertRaises(RuleViolation) as rejected:
            self.repository.commit_player_action(hidden_session["id"], action)
        self.assertEqual(rejected.exception.code, "proposal_intent_mismatch")
        self.assertEqual(self.repository.get_session(hidden_session["id"])["current_revision"], 0)

    def test_replay_rejects_sequence_type_and_turn_metadata_tampering(self):
        gm = FakeGameMaster(self.successful_factory)
        action = self.action()
        self.run_turn(gm, action)
        with closing(connect(self.db_path)) as connection:
            second = connection.execute(
                "SELECT id FROM world_game_events WHERE session_id=? AND sequence=1",
                (self.session["id"],),
            ).fetchone()
            connection.execute(
                "UPDATE world_game_events SET sequence=9 WHERE id=?",
                (second["id"],),
            )
            connection.commit()
        with self.assertRaises(GameDataError):
            self.repository.replay(self.session["id"])
        with self.assertRaises(GameDataError):
            self.run_turn(gm, action)

        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE world_game_events SET sequence=1,event_type='advance_time' WHERE id=?",
                (second["id"],),
            )
            connection.commit()
        with self.assertRaises(GameDataError):
            self.repository.replay(self.session["id"])
        with self.assertRaises(GameDataError):
            self.run_turn(gm, action)

        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE world_game_events SET event_type='discover_fact' WHERE id=?",
                (second["id"],),
            )
            connection.execute(
                "UPDATE world_game_turns SET expected_revision=99 WHERE session_id=?",
                (self.session["id"],),
            )
            connection.commit()
        with self.assertRaises(GameDataError):
            self.repository.replay(self.session["id"])
        with self.assertRaises(GameDataError):
            self.run_turn(gm, action)

        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE world_game_turns SET expected_revision=0,intent_json=? WHERE session_id=?",
                (
                    canonical_json({"type": "move", "destination_id": "home"}),
                    self.session["id"],
                ),
            )
            connection.commit()
        with self.assertRaises(GameDataError):
            self.repository.replay(self.session["id"])
        with self.assertRaises(GameDataError):
            self.run_turn(gm, action)
        self.assertEqual(gm.calls, 1)

    def test_current_state_and_snapshot_json_are_verified_against_their_hashes(self):
        gm = FakeGameMaster(self.successful_factory)
        action = self.action()
        self.run_turn(gm, action)
        with closing(connect(self.db_path)) as connection:
            session_row = connection.execute(
                "SELECT current_state_json FROM world_game_sessions WHERE id=?",
                (self.session["id"],),
            ).fetchone()
            original_current = session_row["current_state_json"]
            tampered = json.loads(original_current)
            tampered["player"]["location_id"] = "home"
            connection.execute(
                "UPDATE world_game_sessions SET current_state_json=? WHERE id=?",
                (canonical_json(tampered), self.session["id"]),
            )
            connection.commit()
        with self.assertRaises(GameDataError):
            self.repository.get_session(self.session["id"])
        recovered = self.repository.replay(self.session["id"])
        self.assertEqual(recovered.player.location_id, "station")
        with self.assertRaises(GameDataError):
            self.repository.replay_result(self.session["id"])
        with self.assertRaises(GameDataError):
            self.run_turn(gm, action)

        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE world_game_sessions SET current_state_json=? WHERE id=?",
                (original_current, self.session["id"]),
            )
            snapshot = connection.execute(
                "SELECT state_json FROM world_game_snapshots WHERE session_id=? AND revision=1",
                (self.session["id"],),
            ).fetchone()
            original_snapshot = snapshot["state_json"]
            tampered_snapshot = json.loads(original_snapshot)
            tampered_snapshot["player"]["location_id"] = "home"
            connection.execute(
                "UPDATE world_game_snapshots SET state_json=? WHERE session_id=? AND revision=1",
                (canonical_json(tampered_snapshot), self.session["id"]),
            )
            connection.commit()
        with self.assertRaises(GameDataError):
            self.repository.replay(self.session["id"])
        with self.assertRaises(GameDataError):
            self.run_turn(gm, action)
        self.assertEqual(gm.calls, 1)


if __name__ == "__main__":
    unittest.main()
