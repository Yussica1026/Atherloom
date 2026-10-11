from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

from backend.game.engine import canonical_json, state_digest
from backend.game.events import DiscoverFactEvent, MoveActorEvent, relationship_id_for
from backend.game.models import (
    AICharacterActionIn,
    AICharacterRef,
    AICharacterResolvedRuntime,
    BranchCreateIn,
    NPCRef,
    PlayerActionIn,
    PlayerRef,
    SaveCreateIn,
    SessionCreateIn,
    WorldCreateIn,
)
from backend.game.repository import (
    GameDataError,
    GameRepository,
    ai_character_runtime_hash,
)
from backend.game.router import create_router
from backend.game.rules import RuleViolation
from backend.game.schema import connect


WORLD_INPUT = {
    "name": "AI 居民测试世界",
    "description": "Player、AICharacter 与 NPC 必须保持独立。",
    "starting_location_id": "home",
    "locations": [
        {"id": "home", "name": "阁楼", "exits": ["station"]},
        {"id": "station", "name": "旧车站", "exits": ["home", "garden"]},
        {"id": "garden", "name": "月光花园", "exits": ["station"]},
    ],
    "items": [
        {
            "id": "ticket",
            "name": "旧车票",
            "initial_location_id": "home",
            "quantity": 1,
        },
        {
            "id": "lantern",
            "name": "提灯",
            "initial_location_id": "station",
            "quantity": 1,
        },
    ],
    "npcs": [
        {
            "id": "keeper",
            "name": "守站人",
            "initial_location_id": "station",
        }
    ],
    "facts": [
        {"id": "last-train", "text": "末班车从未真正离站。"},
    ],
    "quests": [
        {
            "id": "find-platform",
            "title": "寻找月台",
            "initial_status": "available",
        }
    ],
}


def resident_seed(
    actor_id: str = "raven",
    *,
    active: bool = True,
    location_id: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": actor_id,
        "display_name": f"居民-{actor_id}",
        "persona_id": f"persona-{actor_id}",
        "provider_id": f"provider-{actor_id}",
        "active": active,
    }
    if location_id is not None:
        result["initial_location_id"] = location_id
    return result


def resolved_runtime(persona_id: str, provider_id: str) -> AICharacterResolvedRuntime:
    return AICharacterResolvedRuntime.model_validate({
        "persona_id": persona_id,
        "provider_id": provider_id,
        "persona": {
            "name": f"人格-{persona_id}",
            "prompt": "记住你是世界中的独立居民，不是真人玩家，也不是 NPC。",
        },
        "provider": {
            "name": f"模型-{provider_id}",
            "protocol": "openai",
            "model": "local-test-model",
            "temperature": 0.6,
            "top_p": 0.9,
            "max_tokens": 2048,
            "stream_enabled": True,
            "thinking_enabled": False,
        },
    })


class LongWorldAICharacterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "ai-characters.db"
        self.resolver_calls: list[tuple[str, str]] = []

        def resolver(persona_id: str, provider_id: str) -> AICharacterResolvedRuntime:
            self.resolver_calls.append((persona_id, provider_id))
            return resolved_runtime(persona_id, provider_id)

        self.repository = GameRepository(
            self.db_path,
            ai_character_resolver=resolver,
        )
        self.repository.init()
        self.world = self.repository.create_world(WorldCreateIn.model_validate(WORLD_INPUT))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_session(
        self,
        seeds: list[dict[str, object]] | None = None,
        *,
        repository: GameRepository | None = None,
    ) -> dict[str, object]:
        body = SessionCreateIn.model_validate({
            "world_id": self.world["id"],
            "player_id": "player-1",
            "player_display_name": "测试玩家",
            "branch_name": "居民主线",
            "ai_characters": seeds if seeds is not None else [resident_seed()],
        })
        return (repository or self.repository).create_session(body)

    @staticmethod
    def ai_action(
        intent: dict[str, object],
        *,
        actor_id: str = "raven",
        revision: int = 0,
        key: str = "ai-action",
    ) -> AICharacterActionIn:
        return AICharacterActionIn.model_validate({
            "actor": {"kind": "ai_character", "id": actor_id},
            "intent": intent,
            "content": "AI 居民自主选择了一个当前回合动作。",
            "expected_revision": revision,
            "idempotency_key": key,
        })

    @staticmethod
    def player_action(
        intent: dict[str, object],
        *,
        revision: int,
        key: str,
    ) -> PlayerActionIn:
        return PlayerActionIn.model_validate({
            "actor": {"kind": "player", "id": "player-1"},
            "intent": intent,
            "expected_revision": revision,
            "idempotency_key": key,
        })

    def table_counts(self) -> dict[str, int]:
        with closing(connect(self.db_path)) as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "world_game_sessions",
                    "world_game_ai_characters",
                    "world_game_turns",
                    "world_game_events",
                    "world_game_snapshots",
                )
            }

    def test_session_seed_resolves_and_persists_only_non_secret_runtime_snapshots(self):
        session = self.create_session([
            resident_seed("raven"),
            resident_seed("echo", active=False, location_id="station"),
        ])

        self.assertEqual(self.resolver_calls, [
            ("persona-raven", "provider-raven"),
            ("persona-echo", "provider-echo"),
        ])
        self.assertEqual(
            session["state"]["ai_characters"]["raven"],
            {
                "id": "raven",
                "display_name": "居民-raven",
                "persona_id": "persona-raven",
                "provider_id": "provider-raven",
                "runtime_hash": ai_character_runtime_hash(
                    resolved_runtime("persona-raven", "provider-raven")
                ),
                "location_id": "home",
                "active": True,
            },
        )
        self.assertEqual(session["state"]["ai_characters"]["echo"]["location_id"], "station")
        self.assertFalse(session["state"]["ai_characters"]["echo"]["active"])

        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT actor_id,persona_id,provider_id,persona_snapshot_json,config_json
                FROM world_game_ai_characters WHERE session_id=? ORDER BY actor_id
                """,
                (session["id"],),
            ).fetchall()
        self.assertEqual([row["actor_id"] for row in rows], ["echo", "raven"])
        for row in rows:
            runtime = resolved_runtime(row["persona_id"], row["provider_id"])
            self.assertEqual(json.loads(row["persona_snapshot_json"]), runtime.persona.model_dump(mode="json"))
            self.assertEqual(json.loads(row["config_json"]), runtime.provider.model_dump(mode="json"))
            serialized = f"{row['persona_snapshot_json']}\n{row['config_json']}".lower()
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("secret", serialized)
            self.assertNotIn("base_url", serialized)

    def test_seed_and_resolver_reject_injected_credentials_without_partial_rows(self):
        unsafe_seed = resident_seed()
        unsafe_seed["api_key"] = "must-not-enter-world-state"
        with self.assertRaises(ValidationError):
            SessionCreateIn.model_validate({
                "world_id": self.world["id"],
                "player_id": "player-1",
                "player_display_name": "测试玩家",
                "ai_characters": [unsafe_seed],
            })

        def unsafe_resolver(persona_id: str, provider_id: str) -> dict[str, object]:
            runtime = resolved_runtime(persona_id, provider_id).model_dump(mode="json")
            runtime["provider"]["api_key"] = "must-not-be-snapshotted"
            return runtime

        unsafe_repository = GameRepository(
            self.db_path,
            ai_character_resolver=unsafe_resolver,
        )
        with self.assertRaises(RuleViolation) as rejected:
            self.create_session(repository=unsafe_repository)
        self.assertNotIn("must-not-be-snapshotted", str(rejected.exception))
        self.assertEqual(self.table_counts(), {
            "world_game_sessions": 0,
            "world_game_ai_characters": 0,
            "world_game_turns": 0,
            "world_game_events": 0,
            "world_game_snapshots": 0,
        })

    def test_duplicate_collision_and_unknown_location_fail_atomically(self):
        with self.assertRaises(ValidationError):
            SessionCreateIn.model_validate({
                "world_id": self.world["id"],
                "player_id": "player-1",
                "player_display_name": "测试玩家",
                "ai_characters": [resident_seed(), resident_seed()],
            })
        with self.assertRaises(ValidationError):
            SessionCreateIn.model_validate({
                "world_id": self.world["id"],
                "player_id": "player-1",
                "player_display_name": "测试玩家",
                "ai_characters": [resident_seed("player-1")],
            })

        with self.assertRaises(RuleViolation) as npc_collision:
            self.create_session([resident_seed("keeper")])
        self.assertEqual(npc_collision.exception.code, "actor_id_collision")

        with self.assertRaises(RuleViolation) as unknown_location:
            self.create_session([resident_seed(location_id="missing-place")])
        self.assertEqual(unknown_location.exception.code, "location_not_found")
        self.assertEqual(self.table_counts(), {
            "world_game_sessions": 0,
            "world_game_ai_characters": 0,
            "world_game_turns": 0,
            "world_game_events": 0,
            "world_game_snapshots": 0,
        })

    def test_ai_action_schema_allows_only_move_take_drop_and_help(self):
        allowed = [
            {"type": "move", "destination_id": "station"},
            {"type": "take_item", "item_id": "ticket"},
            {"type": "drop_item", "item_id": "ticket"},
            {"type": "help_actor", "target": {"kind": "player", "id": "player-1"}},
        ]
        for index, intent in enumerate(allowed):
            with self.subTest(intent=intent["type"]):
                self.ai_action(intent, key=f"allowed-{index}")

        for forbidden in (
            {"type": "wait", "minutes": 5},
            {"type": "accept_quest", "quest_id": "find-platform"},
        ):
            with self.subTest(forbidden=forbidden["type"]):
                with self.assertRaises(ValidationError):
                    self.ai_action(forbidden)
        with self.assertRaises(ValidationError):
            AICharacterActionIn.model_validate({
                "actor": {"kind": "player", "id": "player-1"},
                "intent": {"type": "move", "destination_id": "station"},
                "expected_revision": 0,
                "idempotency_key": "player-is-not-ai",
            })

    def test_inactive_or_unknown_ai_character_is_rejected_before_any_write(self):
        session = self.create_session([resident_seed(active=False)])
        action = self.ai_action(
            {"type": "move", "destination_id": "station"},
            key="inactive-move",
        )
        with self.assertRaises(RuleViolation) as inactive:
            self.repository.prepare_ai_character_action(session["id"], action)
        self.assertEqual(inactive.exception.code, "ai_character_inactive")

        ghost = self.ai_action(
            {"type": "move", "destination_id": "station"},
            actor_id="ghost",
            key="ghost-move",
        )
        with self.assertRaises(RuleViolation) as missing:
            self.repository.commit_ai_character_action(session["id"], ghost)
        self.assertEqual(missing.exception.code, "ai_character_not_found")
        self.assertEqual(self.repository.get_session(session["id"])["current_revision"], 0)
        self.assertEqual(self.table_counts()["world_game_turns"], 0)

    def test_ai_character_cannot_submit_events_for_player_npc_or_another_ai(self):
        session = self.create_session([resident_seed(), resident_seed("echo")])
        state_hash = session["state_hash"]
        foreign_events = [
            MoveActorEvent(
                event_id="foreign-player",
                actor=PlayerRef(id="player-1"),
                from_location_id="home",
                to_location_id="station",
            ),
            MoveActorEvent(
                event_id="foreign-npc",
                actor=NPCRef(id="keeper"),
                from_location_id="station",
                to_location_id="home",
            ),
            MoveActorEvent(
                event_id="foreign-ai",
                actor=AICharacterRef(id="echo"),
                from_location_id="home",
                to_location_id="station",
            ),
        ]
        for index, event in enumerate(foreign_events):
            action = self.ai_action(
                {"type": "move", "destination_id": "station"},
                key=f"foreign-{index}",
            )
            with self.subTest(actor=event.actor.kind):
                with self.assertRaises(RuleViolation) as rejected:
                    self.repository.commit_ai_character_events(
                        session["id"],
                        action,
                        state_hash,
                        [event],
                    )
                self.assertEqual(rejected.exception.code, "proposal_actor_not_authorized")

        current = self.repository.get_session(session["id"])
        self.assertEqual(current["current_revision"], 0)
        self.assertEqual(current["state"]["player"]["location_id"], "home")
        self.assertEqual(current["state"]["npcs"]["keeper"]["location_id"], "station")
        self.assertEqual(current["state"]["ai_characters"]["echo"]["location_id"], "home")
        self.assertEqual(self.table_counts()["world_game_events"], 0)

    def test_invalid_second_ai_event_rolls_back_the_whole_turn(self):
        session = self.create_session()
        action = self.ai_action(
            {"type": "move", "destination_id": "station"},
            key="move-with-invalid-fact",
        )
        events = [
            MoveActorEvent(
                event_id="valid-first-move",
                actor=AICharacterRef(id="raven"),
                from_location_id="home",
                to_location_id="station",
            ),
            DiscoverFactEvent(
                event_id="invalid-second-fact",
                actor=AICharacterRef(id="raven"),
                fact_id="missing-fact",
            ),
        ]
        with self.assertRaises(RuleViolation):
            self.repository.commit_ai_character_events(
                session["id"],
                action,
                session["state_hash"],
                events,
            )

        current = self.repository.get_session(session["id"])
        self.assertEqual(current["current_revision"], 0)
        self.assertEqual(current["state_hash"], session["state_hash"])
        self.assertEqual(canonical_json(current["state"]), canonical_json(session["state"]))
        self.assertEqual(self.table_counts(), {
            "world_game_sessions": 1,
            "world_game_ai_characters": 1,
            "world_game_turns": 0,
            "world_game_events": 0,
            "world_game_snapshots": 1,
        })

    def test_ai_take_and_drop_use_only_the_residents_own_inventory(self):
        session = self.create_session()
        taken = self.repository.commit_ai_character_action(
            session["id"],
            self.ai_action(
                {"type": "take_item", "item_id": "ticket"},
                key="ai-take-ticket",
            ),
        )
        self.assertEqual(
            taken["state"]["items"]["ticket"]["position"],
            {"kind": "actor", "actor": {"kind": "ai_character", "id": "raven"}},
        )

        dropped = self.repository.commit_ai_character_action(
            session["id"],
            self.ai_action(
                {"type": "drop_item", "item_id": "ticket"},
                revision=1,
                key="ai-drop-ticket",
            ),
        )
        self.assertEqual(
            dropped["state"]["items"]["ticket"]["position"],
            {"kind": "location", "location_id": "home"},
        )
        self.assertEqual(dropped["committed_revision"], 2)

    def test_ai_movement_does_not_mark_player_exploration_as_visited(self):
        session = self.create_session()
        self.assertFalse(session["state"]["locations"]["station"]["visited"])

        moved = self.repository.commit_ai_character_action(
            session["id"],
            self.ai_action(
                {"type": "move", "destination_id": "station"},
                key="ai-explores-alone",
            ),
        )
        self.assertEqual(moved["state"]["ai_characters"]["raven"]["location_id"], "station")
        self.assertEqual(moved["state"]["player"]["location_id"], "home")
        self.assertFalse(moved["state"]["locations"]["station"]["visited"])

    def test_missing_runtime_sidecar_fails_closed_before_ai_turn(self):
        session = self.create_session()
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "DELETE FROM world_game_ai_characters WHERE session_id=? AND actor_id=?",
                (session["id"], "raven"),
            )
            connection.commit()

        with self.assertRaises(GameDataError):
            self.repository.prepare_ai_character_action(
                session["id"],
                self.ai_action(
                    {"type": "move", "destination_id": "station"},
                    key="missing-runtime",
                ),
            )
        with closing(connect(self.db_path)) as connection:
            revision = connection.execute(
                "SELECT current_revision FROM world_game_sessions WHERE id=?",
                (session["id"],),
            ).fetchone()["current_revision"]
        self.assertEqual(revision, 0)

    def test_valid_but_tampered_runtime_snapshot_is_rejected_by_state_bound_hash(self):
        session = self.create_session()
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT config_json FROM world_game_ai_characters
                WHERE session_id=? AND actor_id=?
                """,
                (session["id"], "raven"),
            ).fetchone()
            provider = json.loads(row["config_json"])
            provider["model"] = "tampered-but-schema-valid-model"
            connection.execute(
                """
                UPDATE world_game_ai_characters SET config_json=?
                WHERE session_id=? AND actor_id=?
                """,
                (canonical_json(provider), session["id"], "raven"),
            )
            connection.commit()

        with self.assertRaises(GameDataError):
            self.repository.prepare_ai_character_action(
                session["id"],
                self.ai_action(
                    {"type": "move", "destination_id": "station"},
                    key="tampered-runtime",
                ),
            )

    def test_replay_and_idempotent_retry_reject_tampered_ai_turn_kind(self):
        session = self.create_session()
        action = self.ai_action(
            {"type": "move", "destination_id": "station"},
            key="tamper-ai-kind",
        )
        self.repository.commit_ai_character_action(session["id"], action)
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE world_game_turns SET actor_kind='npc' WHERE session_id=?",
                (session["id"],),
            )
            connection.commit()

        with self.assertRaises(GameDataError):
            self.repository.replay(session["id"])
        with self.assertRaises(GameDataError):
            self.repository.prepare_ai_character_action(session["id"], action)

    def test_prepare_and_late_idempotent_retry_are_actor_aware(self):
        session = self.create_session()
        action = self.ai_action(
            {"type": "move", "destination_id": "station"},
            key="ai-idempotent-move",
        )
        prepared = self.repository.prepare_ai_character_action(session["id"], action)
        self.assertIsNone(prepared.committed)
        self.assertEqual(prepared.state_hash, session["state_hash"])

        first = self.repository.commit_ai_character_action(session["id"], action)
        replayed = self.repository.prepare_ai_character_action(session["id"], action)
        self.assertIsNotNone(replayed.committed)
        self.assertTrue(replayed.committed["idempotent_replay"])
        self.assertEqual(replayed.committed["action_id"], first["action_id"])
        self.assertEqual(replayed.committed["state_hash"], first["state_hash"])

    def test_mixed_player_and_ai_turns_replay_to_the_same_canonical_state_hash(self):
        session = self.create_session()
        session_id = session["id"]
        self.repository.commit_ai_character_action(
            session_id,
            self.ai_action(
                {"type": "take_item", "item_id": "ticket"},
                key="mixed-ai-take",
            ),
        )
        self.repository.commit_player_action(
            session_id,
            self.player_action(
                {"type": "move", "destination_id": "station"},
                revision=1,
                key="mixed-player-move",
            ),
        )
        self.repository.commit_ai_character_action(
            session_id,
            self.ai_action(
                {"type": "move", "destination_id": "station"},
                revision=2,
                key="mixed-ai-move",
            ),
        )
        committed = self.repository.commit_ai_character_action(
            session_id,
            self.ai_action(
                {
                    "type": "help_actor",
                    "target": {"kind": "player", "id": "player-1"},
                },
                revision=3,
                key="mixed-ai-help",
            ),
        )

        current = self.repository.get_session(session_id)
        replayed = self.repository.replay_result(session_id)
        canonical_state = canonical_json(current["state"])
        self.assertEqual(canonical_json(replayed["state"]), canonical_state)
        self.assertEqual(replayed["state_hash"], state_digest(self.repository.replay(session_id)))
        self.assertEqual(replayed["state_hash"], current["state_hash"])
        self.assertEqual(committed["state_hash"], current["state_hash"])
        self.assertTrue(replayed["matches_current"])
        self.assertEqual(current["current_revision"], 4)
        self.assertEqual(current["state"]["turn"], 4)
        self.assertEqual(
            current["state"]["items"]["ticket"]["position"],
            {"kind": "actor", "actor": {"kind": "ai_character", "id": "raven"}},
        )
        relationship_id = relationship_id_for(
            AICharacterRef(id="raven"),
            PlayerRef(id="player-1"),
        )
        self.assertIn(relationship_id, current["state"]["relationships"])
        with closing(connect(self.db_path)) as connection:
            actor_kinds = [row["actor_kind"] for row in connection.execute(
                "SELECT actor_kind FROM world_game_turns WHERE session_id=? ORDER BY revision",
                (session_id,),
            )]
        self.assertEqual(actor_kinds, ["ai_character", "player", "ai_character", "ai_character"])

    def test_save_and_branch_copy_ai_runtime_rows_and_resume_from_saved_state(self):
        session = self.create_session([
            resident_seed(),
            resident_seed("echo", active=False, location_id="garden"),
        ])
        moved = self.repository.commit_ai_character_action(
            session["id"],
            self.ai_action(
                {"type": "move", "destination_id": "station"},
                key="parent-ai-to-station",
            ),
        )
        save = self.repository.create_save(
            session["id"],
            SaveCreateIn(name="AI 居民抵达车站", revision=1),
        )
        self.repository.commit_ai_character_action(
            session["id"],
            self.ai_action(
                {"type": "move", "destination_id": "home"},
                revision=1,
                key="parent-ai-back-home",
            ),
        )

        branch = self.repository.branch_from_save(
            session["id"],
            save["id"],
            BranchCreateIn(branch_name="AI 居民车站分支"),
        )
        self.assertEqual(branch["parent_session_id"], session["id"])
        self.assertEqual(branch["parent_revision"], 1)
        self.assertEqual(branch["current_revision"], 0)
        self.assertEqual(branch["state"]["turn"], moved["state"]["turn"])
        self.assertEqual(branch["state"]["ai_characters"]["raven"]["location_id"], "station")
        self.assertFalse(branch["state"]["ai_characters"]["echo"]["active"])

        with closing(connect(self.db_path)) as connection:
            def runtime_rows(session_id: str) -> list[tuple[object, ...]]:
                return [tuple(row) for row in connection.execute(
                    """
                    SELECT actor_id,persona_id,provider_id,persona_snapshot_json,config_json
                    FROM world_game_ai_characters WHERE session_id=? ORDER BY actor_id
                    """,
                    (session_id,),
                )]

            self.assertEqual(runtime_rows(branch["id"]), runtime_rows(session["id"]))

        branch_commit = self.repository.commit_ai_character_action(
            branch["id"],
            self.ai_action(
                {"type": "move", "destination_id": "home"},
                revision=0,
                key="branch-ai-back-home",
            ),
        )
        self.assertEqual(branch_commit["state"]["ai_characters"]["raven"]["location_id"], "home")
        self.assertTrue(self.repository.replay_result(branch["id"])["matches_current"])
        self.assertEqual(
            self.repository.get_session(session["id"])["state"]["ai_characters"]["raven"]["location_id"],
            "home",
        )

    def test_ai_character_actions_have_no_public_http_route(self):
        router, _initialize = create_router(self.db_path)
        app = FastAPI()
        app.include_router(router)
        specification = app.openapi()
        paths = specification["paths"]
        self.assertIn("/api/game-sessions/{session_id}/actions", paths)
        self.assertFalse(any("ai-action" in path or "ai-character" in path for path in paths))
        action_schema = paths["/api/game-sessions/{session_id}/actions"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]
        self.assertEqual(action_schema["$ref"], "#/components/schemas/PlayerActionIn")
        self.assertNotIn("AICharacterActionIn", specification["components"]["schemas"])


if __name__ == "__main__":
    unittest.main()
