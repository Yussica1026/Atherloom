import ast
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.game.engine import canonical_json
from backend.game.events import relationship_id_for
from backend.game.gm_port import GameMasterResponseError, GameMasterTransportError
from backend.game.models import NPCRef, PlayerRef
from backend.game.repository import GameRepository
from backend.game.router import create_router
from backend.game.schema import MIGRATION_1, connect


WORLD_PAYLOAD = {
    "name": "雾港",
    "description": "一座会记住选择的沿海城镇。",
    "starting_location_id": "home",
    "locations": [
        {"id": "home", "name": "阁楼", "exits": ["station"]},
        {"id": "station", "name": "旧车站", "exits": ["home"]},
        {"id": "garden", "name": "封闭花园", "exits": []},
    ],
    "items": [
        {"id": "ticket", "name": "旧车票", "initial_location_id": "home"}
    ],
    "npcs": [
        {"id": "keeper", "name": "守站人", "initial_location_id": "station"}
    ],
    "facts": [
        {"id": "last-train", "text": "末班车从未真正离站"}
    ],
    "quests": [
        {
            "id": "find-platform",
            "title": "找到旧站台",
            "description": "向守站人确认被封闭的旧站台。",
        }
    ],
}


class ApiFakeGameMaster:
    def __init__(self, narration: str = "雾里传来旧车站的钟声。"):
        self.narration = narration
        self.calls = 0

    async def propose_turn(self, context):
        self.calls += 1
        intent = context.action.intent
        if intent.type == "freeform":
            events = [{
                "type": "advance_time",
                "actor": context.action.actor.model_dump(mode="json"),
                "minutes": 3,
            }]
        else:
            events = [{
                "type": "move_actor",
                "actor": context.action.actor.model_dump(mode="json"),
                "from_location_id": context.state.player.location_id,
                "to_location_id": intent.destination_id,
            }]
        return {
            "schema_version": 1,
            "session_id": context.session_id,
            "based_on_revision": context.based_on_revision,
            "based_on_state_hash": context.based_on_state_hash,
            "initiator": context.action.actor.model_dump(mode="json"),
            "events": events,
            "narration": {"text": self.narration},
        }


class RaisingGameMaster:
    def __init__(self, error: Exception):
        self.error = error

    async def propose_turn(self, _context):
        raise self.error


class LongWorldApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "game.db"
        router, init = create_router(lambda: self.db_path)
        init()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.tempdir.cleanup()

    def create_world_and_session(self):
        world_response = self.client.post("/api/game-worlds", json=WORLD_PAYLOAD)
        self.assertEqual(world_response.status_code, 201, world_response.text)
        world = world_response.json()
        session_response = self.client.post("/api/game-sessions", json={
            "world_id": world["id"],
            "player_id": "player-1",
            "player_display_name": "测试玩家",
            "branch_name": "主线",
        })
        self.assertEqual(session_response.status_code, 201, session_response.text)
        return world, session_response.json()

    def move_payload(self, revision=0, key="move-1", destination="station"):
        return {
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "move", "destination_id": destination},
            "content": "去旧车站",
            "expected_revision": revision,
            "idempotency_key": key,
        }

    def test_lists_worlds_and_sessions_and_filters_by_world(self):
        self.assertEqual(self.client.get("/api/game-worlds").json(), {"worlds": []})
        self.assertEqual(self.client.get("/api/game-sessions").json(), {"sessions": []})

        first_world, first_session = self.create_world_and_session()
        second_world_response = self.client.post(
            "/api/game-worlds",
            json={**WORLD_PAYLOAD, "name": "潮汐镇"},
        )
        self.assertEqual(second_world_response.status_code, 201, second_world_response.text)
        second_world = second_world_response.json()
        second_session_response = self.client.post("/api/game-sessions", json={
            "world_id": second_world["id"],
            "player_id": "player-2",
            "player_display_name": "旅人",
            "branch_name": "潮汐主线",
        })
        self.assertEqual(second_session_response.status_code, 201, second_session_response.text)
        second_session = second_session_response.json()

        worlds_response = self.client.get("/api/game-worlds")
        self.assertEqual(worlds_response.status_code, 200, worlds_response.text)
        worlds = worlds_response.json()["worlds"]
        self.assertEqual({world["id"] for world in worlds}, {first_world["id"], second_world["id"]})
        for world in worlds:
            self.assertEqual(world["session_count"], 1)
            self.assertNotIn("version", world)

        sessions_response = self.client.get("/api/game-sessions")
        self.assertEqual(sessions_response.status_code, 200, sessions_response.text)
        sessions = sessions_response.json()["sessions"]
        self.assertEqual(
            {session["id"] for session in sessions},
            {first_session["id"], second_session["id"]},
        )
        first_summary = next(session for session in sessions if session["id"] == first_session["id"])
        self.assertEqual(first_summary["world_name"], first_world["name"])
        self.assertEqual(first_summary["player"], first_session["state"]["player"])
        self.assertEqual(first_summary["ai_character_count"], 0)
        self.assertNotIn("state", first_summary)

        filtered_response = self.client.get(
            "/api/game-sessions",
            params={"world_id": second_world["id"]},
        )
        self.assertEqual(filtered_response.status_code, 200, filtered_response.text)
        self.assertEqual(
            [session["id"] for session in filtered_response.json()["sessions"]],
            [second_session["id"]],
        )
        self.assertEqual(
            self.client.get(
                "/api/game-sessions",
                params={"world_id": "world_missing"},
            ).json(),
            {"sessions": []},
        )
        self.assertEqual(
            self.client.get("/api/game-sessions", params={"world_id": ""}).status_code,
            422,
        )

    def test_direct_actions_reject_freeform_with_explicit_code_and_zero_writes(self):
        _world, session = self.create_world_and_session()
        payload = {
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "freeform"},
            "content": "我试着问雾里有没有人在。",
            "expected_revision": 0,
            "idempotency_key": "direct-freeform",
        }
        response = self.client.post(
            f"/api/game-sessions/{session['id']}/actions",
            json=payload,
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "freeform_requires_game_master",
        )
        current = self.client.get(f"/api/game-sessions/{session['id']}").json()
        self.assertEqual(current["current_revision"], 0)
        self.assertEqual(
            self.client.get(f"/api/game-sessions/{session['id']}/turns").json(),
            {"turns": []},
        )
        with closing(connect(self.db_path)) as connection:
            self.assertEqual(
                {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "world_game_turns",
                        "world_game_turn_outputs",
                        "world_game_events",
                    )
                },
                {
                    "world_game_turns": 0,
                    "world_game_turn_outputs": 0,
                    "world_game_events": 0,
                },
            )

    def test_gm_turn_accepts_freeform_and_retry_does_not_call_factory_or_model_again(self):
        _world, session = self.create_world_and_session()
        game_master = ApiFakeGameMaster("雾里有人轻轻回应了你。")
        factory_calls: list[str] = []

        def game_master_factory(provider_id: str):
            factory_calls.append(provider_id)
            return game_master

        router, initialize = create_router(
            lambda: self.db_path,
            game_master_factory=game_master_factory,
        )
        initialize()
        local_app = FastAPI()
        local_app.include_router(router)
        client = TestClient(local_app)
        payload = {
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "freeform"},
            "content": "  我向雾里问了一声：有人吗？  ",
            "expected_revision": 0,
            "idempotency_key": "gm-freeform",
        }
        url = f"/api/game-sessions/{session['id']}/gm-turns"

        first = client.post(url, params={"provider_id": "provider-freeform"}, json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["state"]["clock"]["total_minutes"], 3)
        self.assertEqual(first.json()["narration"], {"text": game_master.narration})
        self.assertEqual(game_master.calls, 1)
        self.assertEqual(factory_calls, ["provider-freeform"])

        retried = client.post(url, params={"provider_id": "provider-now-disabled"}, json=payload)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertTrue(retried.json()["idempotent_replay"])
        self.assertEqual(retried.json()["narration"], first.json()["narration"])
        self.assertEqual(game_master.calls, 1)
        self.assertEqual(factory_calls, ["provider-freeform"])

    def test_gm_turn_route_is_lazy_idempotent_and_lists_validated_narration(self):
        _world, session = self.create_world_and_session()
        game_master = ApiFakeGameMaster()
        factory_calls: list[str] = []

        def game_master_factory(provider_id: str):
            factory_calls.append(provider_id)
            return game_master

        router, initialize = create_router(
            lambda: self.db_path,
            game_master_factory=game_master_factory,
        )
        initialize()
        local_app = FastAPI()
        local_app.include_router(router)
        client = TestClient(local_app)
        url = f"/api/game-sessions/{session['id']}/gm-turns"
        payload = self.move_payload(key="gm-route-move")

        first = client.post(url, params={"provider_id": "provider-1"}, json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        first_body = first.json()
        self.assertEqual(first_body["narration"], {"text": game_master.narration})
        self.assertFalse(first_body["idempotent_replay"])
        self.assertEqual(game_master.calls, 1)
        self.assertEqual(factory_calls, ["provider-1"])

        retried = client.post(url, params={"provider_id": "disabled-on-retry"}, json=payload)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertTrue(retried.json()["idempotent_replay"])
        self.assertEqual(retried.json()["narration"], first_body["narration"])
        self.assertEqual(game_master.calls, 1)
        self.assertEqual(factory_calls, ["provider-1"])

        turns_response = client.get(f"/api/game-sessions/{session['id']}/turns")
        self.assertEqual(turns_response.status_code, 200, turns_response.text)
        turns = turns_response.json()["turns"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(
            set(turns[0]),
            {
                "id",
                "session_id",
                "revision",
                "actor",
                "intent",
                "content",
                "events",
                "event_count",
                "narration",
                "created_at",
            },
        )
        self.assertEqual(turns[0]["event_count"], len(turns[0]["events"]))
        self.assertEqual(turns[0]["narration"], first_body["narration"])

        direct_payload = {
            "actor": {"kind": "player", "id": "player-1"},
            "intent": {"type": "wait", "minutes": 2},
            "expected_revision": 1,
            "idempotency_key": "direct-wait-after-gm",
        }
        direct = client.post(
            f"/api/game-sessions/{session['id']}/actions",
            json=direct_payload,
        )
        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertNotIn("narration", direct.json())
        gm_retry_of_direct = client.post(
            url,
            params={"provider_id": "not-resolved-for-retry"},
            json=direct_payload,
        )
        self.assertEqual(gm_retry_of_direct.status_code, 200, gm_retry_of_direct.text)
        self.assertIsNone(gm_retry_of_direct.json()["narration"])
        self.assertEqual(factory_calls, ["provider-1"])
        turns = client.get(f"/api/game-sessions/{session['id']}/turns").json()["turns"]
        self.assertEqual([turn["revision"] for turn in turns], [1, 2])
        self.assertIsNone(turns[1]["narration"])

    def test_unconfigured_gm_route_returns_422_after_preflight_without_writes(self):
        _world, session = self.create_world_and_session()
        response = self.client.post(
            f"/api/game-sessions/{session['id']}/gm-turns",
            params={"provider_id": "missing-provider"},
            json=self.move_payload(key="missing-gm"),
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            self.client.get(f"/api/game-sessions/{session['id']}/turns").json(),
            {"turns": []},
        )
        with closing(connect(self.db_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM world_game_turn_outputs").fetchone()[0],
                0,
            )

    def test_gm_transport_and_response_errors_map_to_502_without_writes(self):
        _world, session = self.create_world_and_session()
        current_error: list[Exception] = [GameMasterTransportError("provider unavailable")]
        router, initialize = create_router(
            lambda: self.db_path,
            game_master_factory=lambda _provider_id: RaisingGameMaster(current_error[0]),
        )
        initialize()
        local_app = FastAPI()
        local_app.include_router(router)
        client = TestClient(local_app)

        for index, error in enumerate((
            GameMasterTransportError("provider unavailable"),
            GameMasterResponseError("invalid structured response"),
        )):
            current_error[0] = error
            response = client.post(
                f"/api/game-sessions/{session['id']}/gm-turns",
                params={"provider_id": "provider-error"},
                json=self.move_payload(key=f"provider-error-{index}"),
            )
            self.assertEqual(response.status_code, 502, response.text)

        self.assertEqual(
            client.get(f"/api/game-sessions/{session['id']}/turns").json(),
            {"turns": []},
        )

    def test_persistent_posts_are_idempotent_by_header_or_android_query(self):
        resolver_calls: list[tuple[str, str]] = []

        def resolver(persona_id: str, provider_id: str):
            resolver_calls.append((persona_id, provider_id))
            return {
                "persona_id": persona_id,
                "provider_id": provider_id,
                "persona": {"name": "渡鸦", "prompt": "独立居民"},
                "provider": {
                    "name": "居民线路",
                    "protocol": "openai",
                    "model": "resident-model",
                },
            }

        idempotent_db = Path(self.tempdir.name) / "persistent-idempotency.db"
        router, initialize = create_router(lambda: idempotent_db, resolver)
        initialize()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        parameters = app.openapi()["paths"]["/api/game-worlds"]["post"]["parameters"]
        self.assertEqual(
            {(parameter["name"], parameter["in"]) for parameter in parameters},
            {("Idempotency-Key", "header"), ("idempotency_key", "query")},
        )

        world_headers = {"Idempotency-Key": "create-world-once"}
        world_response = client.post(
            "/api/game-worlds",
            json=WORLD_PAYLOAD,
            headers=world_headers,
        )
        self.assertEqual(world_response.status_code, 201, world_response.text)
        world = world_response.json()
        repeated_world = client.post(
            "/api/game-worlds",
            params={"idempotency_key": "create-world-once"},
            json=WORLD_PAYLOAD,
        )
        self.assertEqual(repeated_world.status_code, 201, repeated_world.text)
        self.assertEqual(repeated_world.json(), world)
        self.assertEqual(
            client.post(
                "/api/game-worlds",
                json={**WORLD_PAYLOAD, "name": "被冲突请求改名"},
                headers=world_headers,
            ).status_code,
            409,
        )
        self.assertEqual(
            client.post(
                "/api/game-worlds",
                params={"idempotency_key": "query-key"},
                json=WORLD_PAYLOAD,
                headers={"Idempotency-Key": "different-header-key"},
            ).status_code,
            422,
        )

        session_body = {
            "world_id": world["id"],
            "player_id": "player-idempotent",
            "player_display_name": "测试玩家",
            "branch_name": "主线",
            "ai_characters": [{
                "id": "raven-resident",
                "display_name": "渡鸦",
                "persona_id": "persona-raven",
                "provider_id": "provider-raven",
            }],
        }
        session_response = client.post(
            "/api/game-sessions",
            params={"idempotency_key": "create-session-once"},
            json=session_body,
            headers={"Idempotency-Key": "create-session-once"},
        )
        self.assertEqual(session_response.status_code, 201, session_response.text)
        session = session_response.json()
        repeated_session = client.post(
            "/api/game-sessions",
            params={"idempotency_key": "create-session-once"},
            json=session_body,
        )
        self.assertEqual(repeated_session.status_code, 201, repeated_session.text)
        self.assertEqual(repeated_session.json(), session)
        self.assertEqual(resolver_calls, [("persona-raven", "provider-raven")])
        conflicting_session = client.post(
            "/api/game-sessions",
            params={"idempotency_key": "create-session-once"},
            json={**session_body, "branch_name": "冲突分支"},
        )
        self.assertEqual(conflicting_session.status_code, 409, conflicting_session.text)
        self.assertEqual(resolver_calls, [("persona-raven", "provider-raven")])

        save_url = f"/api/game-sessions/{session['id']}/saves"
        save_body = {"name": "抵达之前", "revision": 0}
        save_response = client.post(
            save_url,
            params={"idempotency_key": "create-save-once"},
            json=save_body,
            headers={"Idempotency-Key": "create-save-once"},
        )
        self.assertEqual(save_response.status_code, 201, save_response.text)
        save = save_response.json()
        repeated_save = client.post(
            save_url,
            params={"idempotency_key": "create-save-once"},
            json=save_body,
        )
        self.assertEqual(repeated_save.status_code, 201, repeated_save.text)
        self.assertEqual(repeated_save.json(), save)
        self.assertEqual(
            client.post(
                save_url,
                params={"idempotency_key": "create-save-once"},
                json={**save_body, "name": "冲突存档"},
            ).status_code,
            409,
        )

        branch_url = f"{save_url}/{save['id']}/branch"
        branch_body = {"branch_name": "车站分支"}
        branch_response = client.post(
            branch_url,
            json=branch_body,
            headers={"Idempotency-Key": "create-branch-once"},
        )
        self.assertEqual(branch_response.status_code, 201, branch_response.text)
        branch = branch_response.json()
        repeated_branch = client.post(
            branch_url,
            params={"idempotency_key": "create-branch-once"},
            json=branch_body,
        )
        self.assertEqual(repeated_branch.status_code, 201, repeated_branch.text)
        self.assertEqual(repeated_branch.json(), branch)
        self.assertEqual(
            client.post(
                branch_url,
                params={"idempotency_key": "create-branch-once"},
                json={"branch_name": "冲突世界线"},
            ).status_code,
            409,
        )

        with closing(connect(idempotent_db)) as connection:
            self.assertEqual(
                [
                    (row["operation_type"], row["count"])
                    for row in connection.execute(
                        "SELECT operation_type,COUNT(*) AS count FROM world_game_operations GROUP BY operation_type"
                    )
                ],
                [
                    ("branch_from_save", 1),
                    ("create_save", 1),
                    ("create_session", 1),
                    ("create_world", 1),
                ],
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_worlds").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_versions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_saves").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_sessions").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_ai_characters").fetchone()[0], 2)
            connection.execute(
                """
                CREATE TRIGGER fail_persistent_operation
                BEFORE INSERT ON world_game_operations
                WHEN NEW.idempotency_key='force-operation-failure'
                BEGIN
                  SELECT RAISE(FAIL, 'simulated operation journal failure');
                END
                """
            )
            connection.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            client.post(
                branch_url,
                params={"idempotency_key": "force-operation-failure"},
                json={"branch_name": "必须整体回滚"},
            )
        with closing(connect(idempotent_db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_operations").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_sessions").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_players").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_ai_characters").fetchone()[0], 2)

    def commit_intent(self, session_id, revision, key, intent):
        response = self.client.post(
            f"/api/game-sessions/{session_id}/actions",
            json={
                "actor": {"kind": "player", "id": "player-1"},
                "intent": intent,
                "content": f"regression action {key}",
                "expected_revision": revision,
                "idempotency_key": key,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        committed = response.json()
        self.assertEqual(committed["committed_revision"], revision + 1)
        return committed

    def test_vertical_slice_create_commit_replay_save_and_branch(self):
        world, session = self.create_world_and_session()
        self.assertEqual(session["current_revision"], 0)
        self.assertEqual(session["state"]["player"]["location_id"], "home")
        self.assertEqual(session["state"]["ai_characters"], {})
        self.assertIn("keeper", session["state"]["npcs"])

        commit_response = self.client.post(
            f"/api/game-sessions/{session['id']}/actions",
            json=self.move_payload(),
        )
        self.assertEqual(commit_response.status_code, 200, commit_response.text)
        commit = commit_response.json()
        self.assertEqual(commit["committed_revision"], 1)
        self.assertFalse(commit["idempotent_replay"])
        self.assertEqual(commit["events"][0]["type"], "move_actor")
        self.assertEqual(commit["state"]["player"]["location_id"], "station")

        replay = self.client.get(f"/api/game-sessions/{session['id']}/replay").json()
        self.assertTrue(replay["matches_current"])
        self.assertEqual(replay["state"], commit["state"])
        self.assertEqual(replay["state_hash"], commit["state_hash"])

        save_response = self.client.post(
            f"/api/game-sessions/{session['id']}/saves",
            json={"name": "到达车站", "revision": 1},
        )
        self.assertEqual(save_response.status_code, 201, save_response.text)
        save = save_response.json()
        self.assertEqual(save["revision"], 1)

        wait = self.client.post(
            f"/api/game-sessions/{session['id']}/actions",
            json={
                "actor": {"kind": "player", "id": "player-1"},
                "intent": {"type": "wait", "minutes": 30},
                "expected_revision": 1,
                "idempotency_key": "wait-parent",
            },
        ).json()
        self.assertEqual(wait["committed_revision"], 2)
        self.assertEqual(wait["state"]["clock"]["total_minutes"], 30)

        branch_response = self.client.post(
            f"/api/game-sessions/{session['id']}/saves/{save['id']}/branch",
            json={"branch_name": "车站分支"},
        )
        self.assertEqual(branch_response.status_code, 201, branch_response.text)
        branch = branch_response.json()
        self.assertNotEqual(branch["id"], session["id"])
        self.assertEqual(branch["parent_session_id"], session["id"])
        self.assertEqual(branch["parent_revision"], 1)
        self.assertEqual(branch["current_revision"], 0)
        self.assertEqual(branch["state"]["turn"], 1)
        self.assertEqual(branch["state"]["player"]["location_id"], "station")
        self.assertEqual(branch["state"]["clock"]["total_minutes"], 0)

        branch_wait = self.client.post(
            f"/api/game-sessions/{branch['id']}/actions",
            json={
                "actor": {"kind": "player", "id": "player-1"},
                "intent": {"type": "wait", "minutes": 5},
                "expected_revision": 0,
                "idempotency_key": "wait-branch",
            },
        ).json()
        self.assertEqual(branch_wait["committed_revision"], 1)
        self.assertEqual(branch_wait["state"]["turn"], 2)
        self.assertEqual(branch_wait["state"]["clock"]["total_minutes"], 5)
        self.assertEqual(
            self.client.get(f"/api/game-sessions/{session['id']}").json()["current_revision"],
            2,
        )
        self.assertTrue(
            self.client.get(f"/api/game-sessions/{branch['id']}/replay").json()["matches_current"]
        )

    def test_brutal_replay_is_byte_exact_after_all_supported_fact_changes_and_branch(self):
        _, session = self.create_world_and_session()
        session_id = session["id"]

        actions = [
            ("take-ticket", {"type": "take_item", "item_id": "ticket"}),
            ("move-station", {"type": "move", "destination_id": "station"}),
            (
                "help-keeper",
                {
                    "type": "help_actor",
                    "target": {"kind": "npc", "id": "keeper"},
                },
            ),
            ("accept-platform", {"type": "accept_quest", "quest_id": "find-platform"}),
        ]
        commits = []
        for revision, (key, intent) in enumerate(actions):
            commits.append(self.commit_intent(session_id, revision, key, intent))

        save_response = self.client.post(
            f"/api/game-sessions/{session_id}/saves",
            json={"name": "station-before-drop", "revision": 4},
        )
        self.assertEqual(save_response.status_code, 201, save_response.text)
        save = save_response.json()

        commits.append(self.commit_intent(
            session_id,
            4,
            "drop-ticket",
            {"type": "drop_item", "item_id": "ticket"},
        ))
        commits.append(self.commit_intent(
            session_id,
            5,
            "wait-at-station",
            {"type": "wait", "minutes": 17},
        ))

        event_log = self.client.get(f"/api/game-sessions/{session_id}/events").json()["events"]
        self.assertEqual(
            [entry["type"] for entry in event_log],
            [
                "transfer_item",
                "move_actor",
                "adjust_relationship",
                "advance_quest",
                "transfer_item",
                "advance_time",
            ],
        )
        self.assertEqual([entry["revision"] for entry in event_log], list(range(1, 7)))
        self.assertTrue(all(entry["sequence"] == 0 for entry in event_log))

        current = self.client.get(f"/api/game-sessions/{session_id}").json()
        replay = self.client.get(f"/api/game-sessions/{session_id}/replay").json()
        current_json = canonical_json(current["state"])
        replay_json = canonical_json(replay["state"])
        exact_hash = hashlib.sha256(current_json.encode("utf-8")).hexdigest()
        self.assertEqual(replay_json, current_json)
        self.assertEqual(replay["state"], current["state"])
        self.assertEqual(replay["state_hash"], exact_hash)
        self.assertEqual(current["state_hash"], exact_hash)
        self.assertTrue(replay["matches_current"])
        self.assertEqual(current["current_revision"], 6)
        self.assertEqual(current["state"]["turn"], 6)
        self.assertEqual(current["state"]["player"]["location_id"], "station")
        self.assertEqual(
            current["state"]["items"]["ticket"]["position"],
            {"kind": "location", "location_id": "station"},
        )
        self.assertEqual(
            current["state"]["relationships"][relationship_id_for(
                PlayerRef(id="player-1"),
                NPCRef(id="keeper"),
            )],
            {
                "id": relationship_id_for(
                    PlayerRef(id="player-1"),
                    NPCRef(id="keeper"),
                ),
                "subject": {"kind": "player", "id": "player-1"},
                "object": {"kind": "npc", "id": "keeper"},
                "affinity": 5,
                "trust": 3,
                "fear": 0,
                "respect": 2,
            },
        )
        self.assertEqual(current["state"]["quests"]["find-platform"]["status"], "active")
        self.assertEqual(current["state"]["clock"]["total_minutes"], 17)

        revision_four = self.client.get(
            f"/api/game-sessions/{session_id}/replay", params={"revision": 4}
        ).json()
        revision_four_json = canonical_json(revision_four["state"])
        revision_four_hash = hashlib.sha256(revision_four_json.encode("utf-8")).hexdigest()
        self.assertEqual(save["state_hash"], revision_four_hash)
        self.assertFalse(revision_four["matches_current"])

        with closing(connect(self.db_path)) as connection:
            stored_session = connection.execute(
                "SELECT current_state_json,state_hash FROM world_game_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            stored_snapshot = connection.execute(
                "SELECT state_json,state_hash FROM world_game_snapshots WHERE session_id=? AND revision=6",
                (session_id,),
            ).fetchone()
            stored_save = connection.execute(
                "SELECT state_json,state_hash FROM world_game_saves WHERE id=?",
                (save["id"],),
            ).fetchone()
        self.assertEqual(stored_session["current_state_json"], current_json)
        self.assertEqual(stored_snapshot["state_json"], current_json)
        self.assertEqual(stored_session["state_hash"], exact_hash)
        self.assertEqual(stored_snapshot["state_hash"], exact_hash)
        self.assertEqual(stored_save["state_json"], revision_four_json)
        self.assertEqual(stored_save["state_hash"], revision_four_hash)

        branch_response = self.client.post(
            f"/api/game-sessions/{session_id}/saves/{save['id']}/branch",
            json={"branch_name": "station-counterfactual"},
        )
        self.assertEqual(branch_response.status_code, 201, branch_response.text)
        branch = branch_response.json()
        expected_branch_state = json.loads(revision_four_json)
        expected_branch_state["session_id"] = branch["id"]
        expected_branch_state["revision"] = 0
        branch_json = canonical_json(branch["state"])
        branch_hash = hashlib.sha256(branch_json.encode("utf-8")).hexdigest()
        self.assertEqual(branch_json, canonical_json(expected_branch_state))
        self.assertEqual(branch["state_hash"], branch_hash)
        self.assertEqual(branch["parent_session_id"], session_id)
        self.assertEqual(branch["parent_revision"], 4)
        self.assertEqual(branch["state"]["turn"], 4)
        self.assertEqual(branch["state"]["items"]["ticket"]["position"]["kind"], "actor")

        branch_commit = self.commit_intent(
            branch["id"],
            0,
            "branch-wait",
            {"type": "wait", "minutes": 5},
        )
        branch_replay = self.client.get(
            f"/api/game-sessions/{branch['id']}/replay"
        ).json()
        branch_current_json = canonical_json(branch_commit["state"])
        self.assertEqual(canonical_json(branch_replay["state"]), branch_current_json)
        self.assertEqual(
            branch_replay["state_hash"],
            hashlib.sha256(branch_current_json.encode("utf-8")).hexdigest(),
        )
        self.assertTrue(branch_replay["matches_current"])

        unchanged_parent = self.client.get(f"/api/game-sessions/{session_id}").json()
        self.assertEqual(unchanged_parent["current_revision"], 6)
        self.assertEqual(unchanged_parent["state_hash"], exact_hash)
        self.assertEqual(canonical_json(unchanged_parent["state"]), current_json)

    def test_rules_reject_non_adjacent_move_without_writing(self):
        _, session = self.create_world_and_session()
        response = self.client.post(
            f"/api/game-sessions/{session['id']}/actions",
            json=self.move_payload(destination="garden"),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "locations_not_connected")
        stored = self.client.get(f"/api/game-sessions/{session['id']}").json()
        self.assertEqual(stored["current_revision"], 0)
        self.assertEqual(stored["state"]["player"]["location_id"], "home")
        self.assertEqual(
            self.client.get(f"/api/game-sessions/{session['id']}/events").json()["events"],
            [],
        )

    def test_revision_and_idempotency_guards(self):
        _, session = self.create_world_and_session()
        url = f"/api/game-sessions/{session['id']}/actions"
        first = self.client.post(url, json=self.move_payload())
        self.assertEqual(first.status_code, 200, first.text)
        repeated = self.client.post(url, json=self.move_payload())
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["idempotent_replay"])
        self.assertEqual(repeated.json()["action_id"], first.json()["action_id"])
        self.assertEqual(len(self.client.get(f"/api/game-sessions/{session['id']}/events").json()["events"]), 1)

        reused = self.client.post(url, json=self.move_payload(destination="garden"))
        self.assertEqual(reused.status_code, 409)
        stale = self.client.post(url, json=self.move_payload(key="move-stale"))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(f"/api/game-sessions/{session['id']}").json()["current_revision"], 1)

    def test_sql_failure_rolls_back_turn_events_state_and_revision(self):
        _, session = self.create_world_and_session()
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER fail_revision_one_snapshot
                BEFORE INSERT ON world_game_snapshots
                WHEN NEW.revision=1
                BEGIN
                  SELECT RAISE(FAIL, 'simulated snapshot failure');
                END
                """
            )
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(
                f"/api/game-sessions/{session['id']}/actions",
                json=self.move_payload(),
            )
        with closing(connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_turns").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM world_game_events").fetchone()[0], 0)
            row = connection.execute(
                "SELECT current_revision FROM world_game_sessions WHERE id=?", (session["id"],)
            ).fetchone()
            self.assertEqual(row["current_revision"], 0)

    def test_replay_detects_tampered_event_log(self):
        _, session = self.create_world_and_session()
        response = self.client.post(
            f"/api/game-sessions/{session['id']}/actions",
            json={
                "actor": {"kind": "player", "id": "player-1"},
                "intent": {"type": "wait", "minutes": 5},
                "expected_revision": 0,
                "idempotency_key": "wait-1",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT id,event_json FROM world_game_events WHERE session_id=?", (session["id"],)
            ).fetchone()
            event = json.loads(row["event_json"])
            event["minutes"] = 6
            connection.execute(
                "UPDATE world_game_events SET event_json=? WHERE id=?",
                (json.dumps(event), row["id"]),
            )
            connection.commit()
        replay = self.client.get(f"/api/game-sessions/{session['id']}/replay")
        self.assertEqual(replay.status_code, 500)
        self.assertIn("事件 JSON 不是规范形式", replay.json()["detail"])

    def test_world_schema_rejects_unknown_exit(self):
        invalid = {**WORLD_PAYLOAD, "locations": [
            {"id": "home", "name": "阁楼", "exits": ["missing"]}
        ]}
        response = self.client.post("/api/game-worlds", json=invalid)
        self.assertEqual(response.status_code, 422)

    def test_game_schema_version_is_independent_from_legacy_user_version(self):
        other_db = Path(self.tempdir.name) / "independent.db"
        with closing(sqlite3.connect(other_db)) as connection:
            connection.execute("PRAGMA user_version=17")
            connection.commit()
        repository = GameRepository(other_db)
        repository.init()
        with closing(sqlite3.connect(other_db)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 17)
            self.assertEqual(
                connection.execute("SELECT version FROM world_game_schema_meta").fetchone()[0],
                2,
            )
            connection.execute("DROP TABLE world_game_operations")
            connection.commit()
        repository.init()
        with closing(sqlite3.connect(other_db)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 17)
            self.assertEqual(
                connection.execute("SELECT version FROM world_game_schema_meta").fetchone()[0],
                2,
            )
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_game_operations'"
            ).fetchone())

    def test_schema_v1_database_migrates_to_v2_turn_outputs_without_touching_user_version(self):
        migrated_db = Path(self.tempdir.name) / "v1-to-v2.db"
        with closing(sqlite3.connect(migrated_db)) as connection:
            connection.execute("PRAGMA user_version=23")
            connection.execute(
                """
                CREATE TABLE world_game_schema_meta (
                  singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                  version INTEGER NOT NULL
                )
                """
            )
            for statement in MIGRATION_1:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO world_game_schema_meta(singleton_id,version) VALUES(1,1)"
            )
            connection.commit()

        GameRepository(migrated_db).init()
        with closing(sqlite3.connect(migrated_db)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 23)
            self.assertEqual(
                connection.execute("SELECT version FROM world_game_schema_meta").fetchone()[0],
                2,
            )
            self.assertIsNotNone(connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='world_game_turn_outputs'
                """
            ).fetchone())

    def test_main_app_resolves_ai_runtime_from_safe_persona_provider_allowlist(self):
        with patch.object(app_module, "DB_PATH", self.db_path):
            app_module.init_db()
            provider = app_module.save_provider(app_module.ProviderIn(
                name="居民线路",
                protocol="openai",
                base_url="https://user:password@example.invalid/v1",
                api_key="test-token",
                model="resident-model",
                custom_headers='{"X-Test":"test-header"}',
            ))
            persona = app_module.save_persona(app_module.PersonaIn(
                name="渡鸦居民",
                prompt="你是长期世界里的独立居民。",
                config={
                    "custom_headers": {"x-api-key": "persona-secret-canary"},
                    "mcp_servers": ["private-tool-server"],
                },
            ))
            backup_game_master = ApiFakeGameMaster("备份里的站台回声。")
            router, initialize = create_router(
                lambda: self.db_path,
                app_module.resolve_game_ai_character,
                lambda _provider_id: backup_game_master,
            )
            initialize()
            local_app = FastAPI()
            local_app.include_router(router)
            client = TestClient(local_app)
            world_response = client.post(
                "/api/game-worlds",
                json=WORLD_PAYLOAD,
                headers={"Idempotency-Key": "backup-world-operation"},
            )
            self.assertEqual(world_response.status_code, 201, world_response.text)
            world = world_response.json()
            session_body = {
                "world_id": world["id"],
                "player_id": "player-safe-snapshot",
                "player_display_name": "测试玩家",
                "ai_characters": [{
                    "id": "raven-resident",
                    "display_name": "渡鸦",
                    "persona_id": persona["id"],
                    "provider_id": provider["id"],
                }],
            }
            response = client.post(
                "/api/game-sessions",
                json=session_body,
                headers={"Idempotency-Key": "backup-session-operation"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            session = response.json()

            gm_turn = client.post(
                f"/api/game-sessions/{session['id']}/gm-turns",
                params={"provider_id": provider["id"]},
                json={
                    "actor": {"kind": "player", "id": "player-safe-snapshot"},
                    "intent": {"type": "move", "destination_id": "station"},
                    "content": "走向车站",
                    "expected_revision": 0,
                    "idempotency_key": "backup-gm-turn",
                },
            )
            self.assertEqual(gm_turn.status_code, 200, gm_turn.text)

            with closing(connect(self.db_path)) as connection:
                row = connection.execute(
                    """
                    SELECT persona_snapshot_json,config_json
                    FROM world_game_ai_characters
                    WHERE session_id=? AND actor_id='raven-resident'
                    """,
                    (session["id"],),
                ).fetchone()
            snapshots = f"{row['persona_snapshot_json']}\n{row['config_json']}"
            for secret in (
                "test-token",
                "test-header",
                "persona-secret-canary",
                "private-tool-server",
                "user:password",
            ):
                self.assertNotIn(secret, snapshots)
            self.assertNotIn("api_key", snapshots.lower())
            self.assertEqual(
                json.loads(row["config_json"])["model"],
                "resident-model",
            )

            bundle = app_module.export_backup(
                app_module.BackupExportIn(parts=["games"])
            )
            self.assertTrue(
                set(app_module.LONG_WORLD_BACKUP_TABLES).issubset(bundle["tables"])
            )
            self.assertEqual(
                {
                    row["operation_type"]
                    for row in bundle["tables"]["world_game_operations"]
                },
                {"create_world", "create_session"},
            )
            self.assertEqual(len(bundle["tables"]["world_game_turn_outputs"]), 1)
            self.assertNotIn("test-token", canonical_json(bundle))

            with closing(connect(self.db_path)) as connection:
                connection.execute(
                    "UPDATE world_game_sessions SET branch_name='changed-after-backup' WHERE id=?",
                    (session["id"],),
                )
                connection.commit()
            restored = app_module.restore_backup(app_module.BackupRestoreIn(
                bundle=bundle,
                parts=["games"],
            ))
            self.assertTrue(restored["ok"])
            with closing(connect(self.db_path)) as connection:
                restored_name = connection.execute(
                    "SELECT branch_name FROM world_game_sessions WHERE id=?",
                    (session["id"],),
                ).fetchone()["branch_name"]
            self.assertEqual(restored_name, "主线")
            restored_repository = GameRepository(self.db_path)
            self.assertTrue(
                restored_repository.replay_result(session["id"])["matches_current"]
            )
            self.assertEqual(
                restored_repository.list_turns(session["id"])[0]["narration"],
                {"text": "备份里的站台回声。"},
            )
            restored_retry = client.post(
                "/api/game-sessions",
                json=session_body,
                headers={"Idempotency-Key": "backup-session-operation"},
            )
            self.assertEqual(restored_retry.status_code, 201, restored_retry.text)
            self.assertEqual(restored_retry.json()["id"], session["id"])

            partial_bundle = json.loads(json.dumps(bundle))
            partial_bundle["tables"].pop("world_game_events")
            with self.assertRaises(HTTPException) as partial_error:
                app_module.restore_backup(app_module.BackupRestoreIn(
                    bundle=partial_bundle,
                    parts=["games"],
                ))
            self.assertEqual(partial_error.exception.status_code, 400)

            legacy_bundle = json.loads(json.dumps(bundle))
            for table in app_module.LONG_WORLD_BACKUP_TABLES:
                legacy_bundle["tables"].pop(table, None)
            with closing(connect(self.db_path)) as connection:
                connection.execute(
                    "UPDATE world_game_sessions SET branch_name='preserve-long-world' WHERE id=?",
                    (session["id"],),
                )
                connection.commit()
            app_module.restore_backup(app_module.BackupRestoreIn(
                bundle=legacy_bundle,
                parts=["games"],
            ))
            with closing(connect(self.db_path)) as connection:
                preserved_name = connection.execute(
                    "SELECT branch_name FROM world_game_sessions WHERE id=?",
                    (session["id"],),
                ).fetchone()["branch_name"]
            self.assertEqual(preserved_name, "preserve-long-world")

    def test_main_app_mounts_long_world_routes_without_replacing_old_games(self):
        paths = app_module.app.openapi()["paths"]
        self.assertIn("post", paths["/api/game-worlds"])
        self.assertIn("get", paths["/api/game-worlds"])
        self.assertIn("get", paths["/api/game-sessions"])
        self.assertIn("post", paths["/api/game-sessions/{session_id}/actions"])
        self.assertIn("post", paths["/api/game-sessions/{session_id}/gm-turns"])
        self.assertIn("get", paths["/api/game-sessions/{session_id}/turns"])
        session_list_parameters = paths["/api/game-sessions"]["get"]["parameters"]
        self.assertEqual([parameter["name"] for parameter in session_list_parameters], ["world_id"])
        self.assertFalse(session_list_parameters[0]["required"])
        self.assertIn("get", paths["/api/games"])
        self.assertIn("get", paths["/api/games/{game_id}/state"])
        self.assertIn("post", paths["/api/games/{game_id}/action"])
        self.assertIn("post", paths["/api/games/{game_id}/ai-turn"])
        self.assertIn("post", paths["/api/games/{game_id}/room-chat"])

        legacy_response = TestClient(app_module.app).get("/api/games")
        self.assertEqual(legacy_response.status_code, 200, legacy_response.text)
        legacy_catalog = legacy_response.json()
        self.assertIsInstance(legacy_catalog, list)
        self.assertEqual(legacy_catalog[0]["id"], "homestead")
        self.assertTrue(
            {"quiet_fishing", "claw_machine", "cloud_slots", "star_merge"}.issubset(
                {entry["id"] for entry in legacy_catalog}
            )
        )
        self.assertTrue(
            all({"id", "name", "icon", "status", "description"}.issubset(entry) for entry in legacy_catalog)
        )


class LongWorldDependencyBoundaryTests(unittest.TestCase):
    def test_backend_game_only_imports_its_domain_and_approved_infrastructure(self):
        game_root = Path(__file__).resolve().parents[1] / "backend" / "game"
        violations = []
        allowed_game_imports = {
            "__init__": set(),
            "models": set(),
            "events": {"backend.game.models"},
            "rules": {"backend.game.events", "backend.game.models"},
            "engine": {"backend.game.events", "backend.game.models", "backend.game.rules"},
            "schema": set(),
            "proposals": {
                "backend.game.events",
                "backend.game.models",
                "backend.game.rules",
            },
            "gm_port": {"backend.game.proposals"},
            "repository": {
                "backend.game.engine",
                "backend.game.events",
                "backend.game.models",
                "backend.game.rules",
                "backend.game.schema",
            },
            "service": {
                "backend.game.gm_port",
                "backend.game.models",
                "backend.game.proposals",
                "backend.game.repository",
                "backend.game.rules",
            },
            "router": {
                "backend.game.gm_port",
                "backend.game.models",
                "backend.game.repository",
                "backend.game.rules",
                "backend.game.schema",
                "backend.game.service",
            },
        }
        allowed_external_roots = {
            "__init__": set(),
            "models": {"pydantic"},
            "events": {"pydantic"},
            "rules": set(),
            "engine": set(),
            "schema": set(),
            "proposals": {"pydantic"},
            "gm_port": set(),
            "repository": set(),
            "service": set(),
            "router": {"fastapi", "pydantic"},
        }

        for source_path in sorted(game_root.rglob("*.py")):
            relative_module = source_path.relative_to(game_root).with_suffix("")
            local_name = ".".join(relative_module.parts)
            if local_name not in allowed_game_imports:
                violations.append(f"{local_name}: missing dependency-boundary declaration")
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            imported_modules = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.append(node.module)
            for module in imported_modules:
                root = module.split(".", 1)[0]
                if module == "backend" or (
                    module.startswith("backend.") and not module.startswith("backend.game")
                ):
                    violations.append(f"{source_path.name}: forbidden domain import {module}")
                elif module.startswith("backend.game") and module not in allowed_game_imports[local_name]:
                    violations.append(f"{source_path.name}: forbidden dependency direction {module}")
                elif (
                    root not in sys.stdlib_module_names
                    and root not in allowed_external_roots[local_name]
                    and root != "backend"
                ):
                    violations.append(f"{source_path.name}: unapproved infrastructure import {module}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
