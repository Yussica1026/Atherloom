import ast
import asyncio
import json
import random
import re
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.casual_games.router import create_router
from backend.casual_games.schema import init_schema as init_casual_schema
import backend.app as app_module


class CasualGameApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "casual-games.db"
        self.bindings = {
            "conversation-a": {
                "conversation_id": "conversation-a",
                "persona_id": "persona-a",
                "player_id": "local_user",
            },
            "conversation-without-persona": {
                "conversation_id": "conversation-without-persona",
                "persona_id": None,
                "player_id": "local_user",
            },
        }
        router, init = create_router(
            lambda: self.db_path,
            lambda conversation_id: self.bindings.get(conversation_id),
            persona_exists=lambda persona_id: persona_id == "persona-a",
            random_factory=lambda: random.Random(7),
            expose_persona_action_candidates=True,
        )
        init()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tempdir.cleanup()

    def create_session(self, game_id="tic_tac_toe", options=None):
        response = self.client.post(
            "/api/casual-games/sessions",
            json={
                "game_id": game_id,
                "conversation_id": "conversation-a",
                "options": options or {},
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def action(self, session_id, actor, action, revision, key):
        suffix = "actions" if actor == "user" else "persona-actions"
        return self.client.post(
            f"/api/casual-games/sessions/{session_id}/{suffix}",
            json={
                "action": action,
                "expected_revision": revision,
                "idempotency_key": key,
            },
        )

    def test_catalog_is_only_the_four_registered_casual_games(self):
        response = self.client.get("/api/casual-games")
        self.assertEqual(response.status_code, 200, response.text)
        games = response.json()["games"]
        self.assertEqual(
            [game["id"] for game in games],
            [
                "tic_tac_toe",
                "rock_paper_scissors",
                "bulls_and_cows",
                "twenty_questions",
            ],
        )
        self.assertTrue(all(game["rules_version"] == 1 for game in games))
        self.assertTrue(all("persona_id" not in game for game in games))
        bulls = next(game for game in games if game["id"] == "bulls_and_cows")
        secret_pattern = bulls["options_schema"]["properties"]["user_secret"]["pattern"]
        self.assertIsNotNone(re.fullmatch(secret_pattern, "0123"))
        self.assertIsNone(re.fullmatch(secret_pattern, "1123"))

    def test_schema_v1_migrates_result_delivery_fields_to_v2(self):
        legacy_path = Path(self.tempdir.name) / "casual-v1.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE casual_game_schema_meta (
                  singleton_id INTEGER PRIMARY KEY,
                  version INTEGER NOT NULL
                );
                INSERT INTO casual_game_schema_meta VALUES(1,1);
                CREATE TABLE casual_game_results (
                  id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL UNIQUE,
                  result_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )
        init_casual_schema(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as connection:
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(casual_game_results)"
                )
            }
            version = connection.execute(
                "SELECT version FROM casual_game_schema_meta WHERE singleton_id=1"
            ).fetchone()[0]
        self.assertEqual(version, 2)
        self.assertTrue(
            {"chat_message_id", "memory_id", "memory_decision"}.issubset(columns)
        )

    def test_session_identity_comes_from_the_conversation(self):
        session = self.create_session()
        self.assertEqual(session["persona_id"], "persona-a")
        self.assertEqual(session["player_id"], "local_user")
        self.assertEqual(session["conversation_id"], "conversation-a")
        self.assertEqual(session["current_actor"], "user")
        self.assertEqual(session["revision"], 0)

        spoofed = self.client.post(
            "/api/casual-games/sessions",
            json={
                "game_id": "tic_tac_toe",
                "conversation_id": "conversation-a",
                "persona_id": "attacker-selected-persona",
                "options": {},
            },
        )
        self.assertEqual(spoofed.status_code, 422)

        missing = self.client.post(
            "/api/casual-games/sessions",
            json={
                "game_id": "tic_tac_toe",
                "conversation_id": "missing-conversation",
            },
        )
        self.assertEqual(missing.status_code, 409)
        no_persona = self.client.post(
            "/api/casual-games/sessions",
            json={
                "game_id": "tic_tac_toe",
                "conversation_id": "conversation-without-persona",
            },
        )
        self.assertEqual(no_persona.status_code, 409)

    def test_session_creation_can_be_retried_idempotently(self):
        payload = {
            "game_id": "tic_tac_toe",
            "conversation_id": "conversation-a",
            "options": {},
        }
        first = self.client.post(
            "/api/casual-games/sessions",
            json=payload,
            headers={"Idempotency-Key": "open-tool-call-1"},
        )
        replay = self.client.post(
            "/api/casual-games/sessions",
            json=payload,
            headers={"Idempotency-Key": "open-tool-call-1"},
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["id"], first.json()["id"])
        sessions = self.client.get(
            "/api/casual-games/sessions",
            params={"conversation_id": "conversation-a"},
        ).json()["sessions"]
        self.assertEqual([session["id"] for session in sessions], [first.json()["id"]])

        changed = self.client.post(
            "/api/casual-games/sessions",
            json={**payload, "game_id": "rock_paper_scissors"},
            headers={"Idempotency-Key": "open-tool-call-1"},
        )
        self.assertEqual(changed.status_code, 409)

    def test_tic_tac_toe_vertical_slice_commits_result_and_history(self):
        session = self.create_session()
        turns = (
            ("user", 0),
            ("persona", 3),
            ("user", 1),
            ("persona", 4),
            ("user", 2),
        )
        final = None
        for revision, (actor, position) in enumerate(turns):
            response = self.action(
                session["id"],
                actor,
                {"position": position},
                revision,
                f"turn-{revision + 1}",
            )
            self.assertEqual(response.status_code, 200, response.text)
            final = response.json()
            self.assertEqual(final["revision"], revision + 1)
            self.assertFalse(final["idempotent_replay"])

        self.assertEqual(final["status"], "finished")
        self.assertEqual(final["result"]["outcome"], "user_win")
        self.assertEqual(
            final["result"]["participants"],
            [
                {"kind": "user", "id": "local_user"},
                {"kind": "persona", "id": "persona-a"},
            ],
        )
        loaded = self.client.get(f"/api/casual-games/sessions/{session['id']}").json()
        self.assertEqual(loaded["status"], "finished")
        self.assertEqual(loaded["revision"], 5)
        self.assertIsNotNone(loaded["result_id"])
        self.assertEqual(final["result_id"], loaded["result_id"])

        result = self.client.get(
            f"/api/casual-games/sessions/{session['id']}/result"
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["result"], final["result"])
        trusted_result = self.client.get(
            f"/api/casual-games/results/{final['result_id']}"
        )
        self.assertEqual(trusted_result.status_code, 200, trusted_result.text)
        self.assertEqual(trusted_result.json(), result.json())
        history = self.client.get(
            f"/api/casual-games/sessions/{session['id']}/history"
        ).json()["history"]
        self.assertEqual([entry["revision"] for entry in history], [1, 2, 3, 4, 5])
        self.assertEqual([entry["actor_kind"] for entry in history], [
            "user", "persona", "user", "persona", "user"
        ])
        final_replay = self.action(
            session["id"], "user", {"position": 2}, 4, "turn-5"
        )
        self.assertEqual(final_replay.status_code, 200, final_replay.text)
        self.assertTrue(final_replay.json()["idempotent_replay"])
        rejected_after_finish = self.action(
            session["id"], "user", {"position": 8}, 5, "turn-6"
        )
        self.assertEqual(rejected_after_finish.status_code, 409)

    def test_action_revision_turn_and_idempotency_are_enforced(self):
        session = self.create_session()
        first = self.action(session["id"], "user", {"position": 0}, 0, "same-key")
        self.assertEqual(first.status_code, 200, first.text)
        replay = self.action(session["id"], "user", {"position": 0}, 0, "same-key")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["revision"], 1)

        changed = self.action(session["id"], "user", {"position": 1}, 0, "same-key")
        self.assertEqual(changed.status_code, 409)
        stale = self.action(session["id"], "persona", {"position": 4}, 0, "persona-stale")
        self.assertEqual(stale.status_code, 409)
        wrong_turn = self.action(session["id"], "user", {"position": 2}, 1, "wrong-turn")
        self.assertEqual(wrong_turn.status_code, 422)
        self.assertEqual(wrong_turn.json()["detail"]["code"], "not_actor_turn")
        advanced = self.action(
            session["id"], "persona", {"position": 4}, 1, "persona-valid"
        )
        self.assertEqual(advanced.status_code, 200, advanced.text)
        late_replay = self.action(
            session["id"], "user", {"position": 0}, 0, "same-key"
        )
        self.assertEqual(late_replay.status_code, 200, late_replay.text)
        self.assertTrue(late_replay.json()["idempotent_replay"])
        self.assertEqual(late_replay.json()["revision"], 1)

    def test_no_new_action_can_continue_after_chat_switches_identity(self):
        user_turn_session = self.create_session()
        self.bindings["conversation-a"] = {
            "conversation_id": "conversation-a",
            "persona_id": "persona-b",
            "player_id": "local_user",
        }
        user_turn = self.action(
            user_turn_session["id"], "user", {"position": 0}, 0, "user-blocked"
        )
        self.assertEqual(user_turn.status_code, 409)

        self.bindings["conversation-a"] = {
            "conversation_id": "conversation-a",
            "persona_id": "persona-a",
            "player_id": "local_user",
        }
        persona_turn_session = self.create_session()
        first = self.action(
            persona_turn_session["id"], "user", {"position": 0}, 0, "user-1"
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.bindings["conversation-a"] = {
            "conversation_id": "conversation-a",
            "persona_id": "persona-b",
            "player_id": "local_user",
        }
        persona_turn = self.action(
            persona_turn_session["id"], "persona", {"position": 4}, 1, "persona-1"
        )
        self.assertEqual(persona_turn.status_code, 409)
        self.assertIn("切换 Persona", persona_turn.json()["detail"])

    def test_hidden_game_facts_never_appear_in_public_session_state(self):
        rps = self.create_session("rock_paper_scissors")
        committed = self.action(
            rps["id"], "user", {"choice": "rock"}, 0, "rps-rock"
        )
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertNotIn("user_choice", committed.json()["state"])
        self.assertTrue(committed.json()["state"]["user_choice_committed"])
        replay = self.action(
            rps["id"], "user", {"choice": "rock"}, 0, "rps-rock"
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertNotIn("user_choice", replay.json()["state"])
        loaded_rps = self.client.get(
            f"/api/casual-games/sessions/{rps['id']}"
        ).json()
        self.assertNotIn("user_choice", loaded_rps["state"])
        listed_rps = self.client.get(
            "/api/casual-games/sessions",
            params={"conversation_id": "conversation-a"},
        ).json()["sessions"]
        self.assertEqual(len(listed_rps), 1)
        self.assertNotIn("user_choice", listed_rps[0]["state"])
        pending_history = self.client.get(
            f"/api/casual-games/sessions/{rps['id']}/history"
        ).json()["history"]
        self.assertEqual(pending_history[0]["action"], {"choice_committed": True})
        self.assertNotIn(
            '"choice": "rock"',
            json.dumps(pending_history, ensure_ascii=False),
        )
        self.assertNotIn("rps-rock", json.dumps(pending_history, ensure_ascii=False))
        self.assertNotIn("idempotency_key", pending_history[0])
        self.assertNotIn("expected_revision", pending_history[0])
        self.assertNotIn("response", pending_history[0])
        pending_result = self.client.get(
            f"/api/casual-games/sessions/{rps['id']}/result"
        )
        self.assertEqual(pending_result.status_code, 404)

        resolved = self.action(
            rps["id"], "persona", {"choice": "scissors"}, 1, "rps-persona"
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["state"]["user_choice"], "rock")
        self.assertEqual(resolved.json()["state"]["persona_choice"], "scissors")
        self.assertEqual(resolved.json()["result"]["outcome"], "user_win")
        trusted_result = self.client.get(
            f"/api/casual-games/results/{resolved.json()['result_id']}"
        )
        self.assertEqual(trusted_result.status_code, 200, trusted_result.text)
        self.assertEqual(
            trusted_result.json()["result"]["notable_events"],
            ["user:rock", "persona:scissors"],
        )
        revealed_history = self.client.get(
            f"/api/casual-games/sessions/{rps['id']}/history"
        ).json()["history"]
        self.assertEqual(revealed_history[0]["action"], {"choice": "rock"})
        self.assertEqual(revealed_history[1]["action"], {"choice": "scissors"})

        bulls = self.create_session("bulls_and_cows", {"user_secret": "0123"})
        self.assertNotIn("user_secret", bulls["state"])
        self.assertNotIn("persona_secret", bulls["state"])
        loaded_bulls = self.client.get(
            f"/api/casual-games/sessions/{bulls['id']}"
        ).json()
        self.assertNotIn("user_secret", loaded_bulls["state"])
        self.assertNotIn("persona_secret", loaded_bulls["state"])

    def test_behavior_prompt_fields_are_empty_and_memory_defaults_to_ask(self):
        default = self.client.get(
            "/api/casual-games/behavior-configs/persona-a/tic_tac_toe"
        )
        self.assertEqual(default.status_code, 200, default.text)
        self.assertEqual(
            default.json(),
            {
                "instructions": None,
                "strategy_instructions": None,
                "reaction_instructions": None,
                "memory_instructions": None,
                "memory_mode": "ask",
                "persona_id": "persona-a",
                "game_id": "tic_tac_toe",
                "updated_at": None,
            },
        )
        configured = self.client.put(
            "/api/casual-games/behavior-configs/persona-a/tic_tac_toe",
            json={
                "instructions": "按我自己的规则交流",
                "strategy_instructions": "优先控制中心",
                "reaction_instructions": None,
                "memory_instructions": "只记有趣的连胜",
                "memory_mode": "auto",
            },
        )
        self.assertEqual(configured.status_code, 200, configured.text)
        self.assertEqual(configured.json()["instructions"], "按我自己的规则交流")
        self.assertEqual(configured.json()["memory_mode"], "auto")

    def test_unknown_games_and_abandon_conflicts_are_explicit(self):
        unknown = self.client.post(
            "/api/casual-games/sessions",
            json={"game_id": "old_game", "conversation_id": "conversation-a"},
        )
        self.assertEqual(unknown.status_code, 404)
        session = self.create_session()
        abandoned = self.client.post(
            f"/api/casual-games/sessions/{session['id']}/abandon",
            params={"expected_revision": 0},
        )
        self.assertEqual(abandoned.status_code, 200, abandoned.text)
        self.assertEqual(abandoned.json()["status"], "abandoned")
        self.assertEqual(abandoned.json()["state"]["status"], "abandoned")
        self.assertIsNone(abandoned.json()["state"]["turn"])
        self.assertIsNone(abandoned.json()["current_actor"])
        action = self.action(session["id"], "user", {"position": 0}, 0, "late")
        self.assertEqual(action.status_code, 409)


class CasualGameDependencyBoundaryTests(unittest.TestCase):
    def test_domain_does_not_import_legacy_games_longworld_or_app(self):
        root = Path(__file__).resolve().parents[1] / "backend" / "casual_games"
        forbidden = ("backend.app", "backend.game")
        violations = []
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if any(
                        module == prefix or module.startswith(prefix + ".")
                        for prefix in forbidden
                    ):
                        violations.append(f"{source.name}: {module}")
        self.assertEqual(violations, [])


class CasualGameMainAppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = app_module.DB_PATH
        app_module.DB_PATH = Path(self.tempdir.name) / "main-app.db"
        app_module.init_db()
        app_module.init_game_schema()
        app_module.init_casual_game_schema()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        app_module.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def create_bound_session(self, game_id="tic_tac_toe", options=None):
        provider = self.client.post(
            "/api/providers",
            json={
                "name": "Casual Provider",
                "protocol": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "test-key",
                "model": "casual-model",
            },
        ).json()
        persona = self.client.post(
            "/api/personas",
            json={"name": "Current Persona", "prompt": "user-owned persona text"},
        ).json()
        conversation = self.client.post(
            "/api/conversations",
            json={
                "title": "Current chat",
                "provider_id": provider["id"],
                "persona_id": persona["id"],
            },
        ).json()
        response = self.client.post(
            "/api/casual-games/sessions",
            json={
                "game_id": game_id,
                "conversation_id": conversation["id"],
                "options": options or {},
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return persona, conversation, response.json()

    def finish_user_win(self, session):
        service = app_module.casual_game_service()
        user_one = self.client.post(
            f"/api/casual-games/sessions/{session['id']}/actions",
            json={
                "action": {"position": 0},
                "expected_revision": 0,
                "idempotency_key": "finish-user-1",
            },
        )
        self.assertEqual(user_one.status_code, 200, user_one.text)
        service.commit_persona_action(
            session["id"],
            app_module.CasualActionCommitIn(
                action={"position": 3},
                expected_revision=1,
                idempotency_key="finish-persona-1",
            ),
        )
        user_two = self.client.post(
            f"/api/casual-games/sessions/{session['id']}/actions",
            json={
                "action": {"position": 1},
                "expected_revision": 2,
                "idempotency_key": "finish-user-2",
            },
        )
        self.assertEqual(user_two.status_code, 200, user_two.text)
        service.commit_persona_action(
            session["id"],
            app_module.CasualActionCommitIn(
                action={"position": 4},
                expected_revision=3,
                idempotency_key="finish-persona-2",
            ),
        )
        final = self.client.post(
            f"/api/casual-games/sessions/{session['id']}/actions",
            json={
                "action": {"position": 2},
                "expected_revision": 4,
                "idempotency_key": "finish-user-3",
            },
        )
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(final.json()["status"], "finished")
        return final.json()

    def test_main_app_mounts_casual_namespace_without_changing_legacy_routes(self):
        paths = app_module.app.openapi()["paths"]
        self.assertIn("get", paths["/api/casual-games"])
        self.assertIn("post", paths["/api/casual-games/sessions"])
        self.assertIn("post", paths["/api/casual-games/sessions/{session_id}/actions"])
        self.assertIn(
            "post",
            paths["/api/casual-games/sessions/{session_id}/persona-turn"],
        )
        self.assertNotIn(
            "/api/casual-games/sessions/{session_id}/persona-actions",
            paths,
        )
        self.assertIn("get", paths["/api/games"])
        self.assertIn("post", paths["/api/games/{game_id}/action"])

        legacy_before = self.client.get("/api/games").json()
        persona, conversation, session = self.create_bound_session()
        self.assertEqual(session["persona_id"], persona["id"])
        self.assertEqual(session["conversation_id"], conversation["id"])
        self.assertEqual(self.client.get("/api/games").json(), legacy_before)

        ghost_conversation = self.client.post(
            "/api/conversations",
            json={"title": "Ghost", "persona_id": "missing-persona"},
        ).json()
        ghost_session = self.client.post(
            "/api/casual-games/sessions",
            json={
                "game_id": "tic_tac_toe",
                "conversation_id": ghost_conversation["id"],
            },
        )
        self.assertEqual(ghost_session.status_code, 409)
        self.assertEqual(
            self.client.get(
                "/api/casual-games/sessions",
                params={"conversation_id": ghost_conversation["id"]},
            ).json()["sessions"],
            [],
        )

    def test_open_game_tool_creates_one_trusted_current_persona_session(self):
        persona, conversation, _ = self.create_bound_session()
        tools, _ = app_module.builtin_tool_catalog({})
        spec = next(tool for tool in tools if tool["name"] == "atherloom_open_game")
        self.assertEqual(
            spec["input_schema"]["properties"]["game_id"]["enum"],
            list(app_module.CASUAL_CHAT_GAME_IDS),
        )
        self.assertNotIn("user_secret", spec["input_schema"]["properties"])
        arguments = {
            "game_id": "tic_tac_toe",
            "persona_id": "model-spoof",
            "_persona_key": "model-spoof",
            "_conversation_id": conversation["id"],
            "_source_message_id": "invite-message",
        }
        first = asyncio.run(app_module.invoke_builtin_tool("open_game", arguments))
        replay = asyncio.run(app_module.invoke_builtin_tool("open_game", arguments))
        effect = first["effect"]
        self.assertEqual(
            effect,
            {
                "type": "open_game",
                "game_id": "tic_tac_toe",
                "session_id": effect["session_id"],
                "conversation_id": conversation["id"],
                "persona_id": persona["id"],
            },
        )
        self.assertEqual(replay["effect"]["session_id"], effect["session_id"])
        stored = self.client.get(
            f"/api/casual-games/sessions/{effect['session_id']}"
        ).json()
        self.assertEqual(stored["persona_id"], persona["id"])
        rps = asyncio.run(
            app_module.invoke_builtin_tool(
                "open_game",
                {**arguments, "game_id": "rock_paper_scissors"},
            )
        )["effect"]
        self.assertEqual(rps["game_id"], "rock_paper_scissors")
        self.assertEqual(rps["conversation_id"], conversation["id"])
        self.assertEqual(rps["persona_id"], persona["id"])

        sessions_before_setup = self.client.get(
            "/api/casual-games/sessions",
            params={"conversation_id": conversation["id"]},
        ).json()["sessions"]
        bulls = asyncio.run(
            app_module.invoke_builtin_tool(
                "open_game",
                {**arguments, "game_id": "bulls_and_cows"},
            )
        )
        self.assertTrue(bulls["opened"])
        self.assertEqual(
            bulls["effect"],
            {
                "type": "open_game",
                "game_id": "bulls_and_cows",
                "session_id": None,
                "conversation_id": conversation["id"],
                "persona_id": persona["id"],
                "setup_required": True,
            },
        )
        sessions_after_setup = self.client.get(
            "/api/casual-games/sessions",
            params={"conversation_id": conversation["id"]},
        ).json()["sessions"]
        self.assertEqual(sessions_after_setup, sessions_before_setup)

        twenty_questions = asyncio.run(
            app_module.invoke_builtin_tool(
                "open_game",
                {**arguments, "game_id": "twenty_questions"},
            )
        )["effect"]
        self.assertEqual(twenty_questions["game_id"], "twenty_questions")
        self.assertEqual(twenty_questions["conversation_id"], conversation["id"])
        self.assertEqual(twenty_questions["persona_id"], persona["id"])
        twenty_session = self.client.get(
            f"/api/casual-games/sessions/{twenty_questions['session_id']}"
        ).json()
        self.assertEqual(twenty_session["current_actor"], "persona")

        with self.assertRaisesRegex(ValueError, "猜数字和二十问"):
            asyncio.run(
                app_module.invoke_builtin_tool(
                    "open_game",
                    {**arguments, "game_id": "unknown"},
                )
            )

    def test_chat_stream_emits_trusted_open_game_effect(self):
        provider = self.client.post(
            "/api/providers",
            json={
                "name": "Default Persona Provider",
                "protocol": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "test-key",
                "model": "casual-model",
            },
        ).json()
        conversation = self.client.post(
            "/api/conversations",
            json={
                "title": "Default Persona chat",
                "provider_id": provider["id"],
            },
        ).json()
        self.assertIsNone(conversation["persona_id"])

        class FakeResponse:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class FakeAsyncClient:
            requests = []

            def __init__(self, *args, **kwargs):
                self.calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, *args, **kwargs):
                self.calls += 1
                type(self).requests.append(kwargs.get("json"))
                if self.calls == 1:
                    return FakeResponse(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": None,
                                        "tool_calls": [
                                            {
                                                "id": "open-game-call",
                                                "type": "function",
                                                "function": {
                                                    "name": "atherloom_open_game",
                                                    "arguments": json.dumps(
                                                        {
                                                            "game_id": "bulls_and_cows",
                                                            "persona_id": "model-spoof",
                                                        }
                                                    ),
                                                },
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    )
                return FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "猜数字设置已经打开。",
                                }
                            }
                        ]
                    }
                )

        with patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "content": "陪我玩猜数字",
                    "provider_id": conversation["provider_id"],
                    "persona_id": None,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        chunks = [json.loads(line) for line in response.text.splitlines() if line]
        effect = next(
            chunk["tool_event"]["effect"]
            for chunk in chunks
            if chunk.get("tool_event", {}).get("effect", {}).get("type")
            == "open_game"
        )
        self.assertEqual(effect["conversation_id"], conversation["id"])
        self.assertEqual(effect["persona_id"], "__default__")
        self.assertEqual(effect["game_id"], "bulls_and_cows")
        self.assertIsNone(effect["session_id"])
        self.assertTrue(effect["setup_required"])
        self.assertIn("bulls_and_cows", json.dumps(FakeAsyncClient.requests[0]))
        self.assertIn("twenty_questions", json.dumps(FakeAsyncClient.requests[0]))
        sessions = self.client.get(
            "/api/casual-games/sessions",
            params={"conversation_id": conversation["id"]},
        ).json()["sessions"]
        self.assertFalse(
            any(item["game_id"] == "bulls_and_cows" for item in sessions)
        )
        messages = self.client.get(
            f"/api/conversations/{conversation['id']}/messages"
        ).json()
        assistant = next(message for message in messages if message["role"] == "assistant")
        stored_event = assistant["tool_events"][0]
        self.assertEqual(stored_event["effect"], effect)
        self.assertEqual(stored_event["name"], "atherloom_open_game")
        self.assertEqual(stored_event["tool_name"], "打开猜数字")
        self.assertEqual(stored_event["status"], "已打开")

    def test_default_persona_chat_tool_session_can_take_persona_turn(self):
        provider = self.client.post(
            "/api/providers",
            json={
                "name": "Default Persona Turn Provider",
                "protocol": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "test-key",
                "model": "casual-model",
            },
        ).json()
        conversation = self.client.post(
            "/api/conversations",
            json={
                "title": "Default Persona game",
                "provider_id": provider["id"],
            },
        ).json()
        opened = asyncio.run(
            app_module.invoke_builtin_tool(
                "open_game",
                {
                    "game_id": "twenty_questions",
                    "_conversation_id": conversation["id"],
                    "_source_message_id": "default-persona-invite",
                },
            )
        )
        self.assertTrue(opened["opened"])
        self.assertFalse(opened["effect"].get("setup_required", False))
        self.assertEqual(opened["effect"]["persona_id"], "__default__")
        session_id = opened["effect"]["session_id"]
        session = self.client.get(
            f"/api/casual-games/sessions/{session_id}"
        ).json()
        self.assertEqual(session["persona_id"], "__default__")
        self.assertEqual(session["current_actor"], "persona")

        seen_turn = {}

        async def persona_model(provider, system, prompt, **kwargs):
            seen_turn.update(system=system, prompt=prompt)
            return json.dumps(
                {"kind": "question", "text": "它是生物吗？"},
                ensure_ascii=False,
            )

        with patch.object(app_module, "roleplay_model_once", persona_model):
            turn = self.client.post(
                f"/api/casual-games/sessions/{session_id}/persona-turn",
                json={
                    "expected_revision": 0,
                    "idempotency_key": "default-persona-turn",
                },
            )
        self.assertEqual(turn.status_code, 200, turn.text)
        self.assertEqual(turn.json()["current_actor"], "user")
        persona_source = next(
            source
            for source in turn.json()["prompt_sources"]
            if source["kind"] == "persona"
        )
        self.assertEqual(persona_source["persona_id"], "__default__")
        self.assertFalse(persona_source["configured"])
        self.assertNotIn("<persona_instructions>", seen_turn["system"])
        self.assertIn("twenty_questions", seen_turn["system"])

    def test_persona_turn_uses_current_context_and_never_falls_back(self):
        persona, conversation, session = self.create_bound_session()
        self.client.post(
            "/api/memories",
            json={
                "title": "tic_tac_toe memory marker",
                "content": "memory-context-marker",
                "kind": "event",
                "persona_key": persona["id"],
            },
        )
        configured = self.client.put(
            f"/api/casual-games/behavior-configs/{persona['id']}/tic_tac_toe",
            json={
                "instructions": "game-instructions-marker",
                "strategy_instructions": "strategy-marker",
                "memory_mode": "ask",
            },
        )
        self.assertEqual(configured.status_code, 200, configured.text)
        user_turn = self.client.post(
            f"/api/casual-games/sessions/{session['id']}/actions",
            json={
                "action": {"position": 0},
                "expected_revision": 0,
                "idempotency_key": "context-user-turn",
            },
        )
        self.assertEqual(user_turn.status_code, 200, user_turn.text)
        replacement_provider = self.client.post(
            "/api/providers",
            json={
                "name": "Replacement Casual Provider",
                "protocol": "openai",
                "base_url": "https://replacement.example/v1",
                "api_key": "replacement-key",
                "model": "casual-model-2",
            },
        ).json()
        changed = self.client.patch(
            f"/api/conversations/{conversation['id']}",
            json={"provider_id": replacement_provider["id"]},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        seen = {}

        async def fake_model(provider, system, prompt, **kwargs):
            seen.update(
                provider=provider,
                system=system,
                prompt=prompt,
                kwargs=kwargs,
            )
            return '{"position":4}'

        with patch.object(app_module, "roleplay_model_once", fake_model), patch.object(
            app_module,
            "fallback_ai_game_choice",
            side_effect=AssertionError("fallback must not run"),
        ):
            response = self.client.post(
                f"/api/casual-games/sessions/{session['id']}/persona-turn",
                json={
                    "expected_revision": 1,
                    "idempotency_key": "persona-model-turn",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["state"]["board"][4], "O")
        self.assertEqual(payload["revision"], 2)
        self.assertEqual(seen["provider"]["model"], "casual-model-2")
        self.assertIn("user-owned persona text", seen["system"])
        self.assertIn("memory-context-marker", seen["system"])
        self.assertIn("game-instructions-marker", seen["system"])
        self.assertIn("strategy-marker", seen["system"])
        self.assertIn('"legal_positions"', seen["system"])
        self.assertNotIn("reaction_instructions", seen["system"])

        async def must_not_call(*args, **kwargs):
            raise AssertionError("idempotent retry must not call the model")

        with patch.object(app_module, "roleplay_model_once", must_not_call):
            replay = self.client.post(
                f"/api/casual-games/sessions/{session['id']}/persona-turn",
                json={
                    "expected_revision": 1,
                    "idempotency_key": "persona-model-turn",
                },
            )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])

    def test_persona_turn_rejects_invalid_model_action_without_fallback(self):
        _, _, session = self.create_bound_session()
        self.client.post(
            f"/api/casual-games/sessions/{session['id']}/actions",
            json={
                "action": {"position": 0},
                "expected_revision": 0,
                "idempotency_key": "invalid-user-turn",
            },
        )

        async def occupied_position(*args, **kwargs):
            return '{"position":0}'

        with patch.object(app_module, "roleplay_model_once", occupied_position), patch.object(
            app_module,
            "fallback_ai_game_choice",
            side_effect=AssertionError("fallback must not run"),
        ):
            response = self.client.post(
                f"/api/casual-games/sessions/{session['id']}/persona-turn",
                json={
                    "expected_revision": 1,
                    "idempotency_key": "invalid-persona-turn",
                },
            )
        self.assertEqual(response.status_code, 422, response.text)
        stored = self.client.get(
            f"/api/casual-games/sessions/{session['id']}"
        ).json()
        self.assertEqual(stored["revision"], 1)
        self.assertEqual(stored["current_actor"], "persona")

    def test_rock_paper_scissors_trusted_persona_result_reply_and_memory(self):
        persona, conversation, session = self.create_bound_session(
            "rock_paper_scissors"
        )
        memory = self.client.post(
            "/api/memories",
            json={
                "title": "rock_paper_scissors memory marker",
                "content": "rps-memory-context-marker",
                "kind": "event",
                "persona_key": persona["id"],
            },
        )
        self.assertEqual(memory.status_code, 200, memory.text)
        behavior = self.client.put(
            f"/api/casual-games/behavior-configs/{persona['id']}/rock_paper_scissors",
            json={
                "instructions": "rps-game-instructions-marker",
                "strategy_instructions": "rps-strategy-marker",
                "reaction_instructions": "rps-reaction-marker",
                "memory_mode": "ask",
            },
        )
        self.assertEqual(behavior.status_code, 200, behavior.text)

        user_turn = self.client.post(
            f"/api/casual-games/sessions/{session['id']}/actions",
            json={
                "action": {"choice": "rock"},
                "expected_revision": 0,
                "idempotency_key": "rps-user-rock",
            },
        )
        self.assertEqual(user_turn.status_code, 200, user_turn.text)
        self.assertNotIn("user_choice", user_turn.json()["state"])
        self.assertEqual(user_turn.json()["events"], [
            {"type": "choice_committed", "actor": "user"}
        ])
        public_session = self.client.get(
            f"/api/casual-games/sessions/{session['id']}"
        ).json()
        self.assertNotIn("user_choice", public_session["state"])
        self.assertTrue(public_session["state"]["user_choice_committed"])
        public_history = self.client.get(
            f"/api/casual-games/sessions/{session['id']}/history"
        ).json()["history"]
        self.assertEqual(public_history[0]["action"], {"choice_committed": True})

        seen_turn = {}

        async def persona_model(provider, system, prompt, **kwargs):
            seen_turn.update(
                provider=provider,
                system=system,
                prompt=prompt,
                kwargs=kwargs,
            )
            return '{"choice":"scissors"}'

        with patch.object(app_module, "roleplay_model_once", persona_model), patch.object(
            app_module,
            "fallback_ai_game_choice",
            side_effect=AssertionError("fallback must not run"),
        ):
            persona_turn = self.client.post(
                f"/api/casual-games/sessions/{session['id']}/persona-turn",
                json={
                    "expected_revision": 1,
                    "idempotency_key": "rps-persona-scissors",
                },
            )
        self.assertEqual(persona_turn.status_code, 200, persona_turn.text)
        payload = persona_turn.json()
        self.assertEqual(payload["status"], "finished")
        self.assertEqual(payload["state"]["user_choice"], "rock")
        self.assertEqual(payload["state"]["persona_choice"], "scissors")
        self.assertEqual(payload["result"]["outcome"], "user_win")
        self.assertEqual(
            payload["events"][0],
            {
                "type": "choices_revealed",
                "user_choice": "rock",
                "persona_choice": "scissors",
            },
        )
        self.assertEqual(seen_turn["provider"]["id"], conversation["provider_id"])
        self.assertIn("user-owned persona text", seen_turn["system"])
        self.assertIn("rps-memory-context-marker", seen_turn["system"])
        self.assertIn("rps-game-instructions-marker", seen_turn["system"])
        self.assertIn("rps-strategy-marker", seen_turn["system"])
        self.assertNotIn("rps-reaction-marker", seen_turn["system"])
        self.assertIn('"user_choice_committed": true', seen_turn["system"])
        self.assertNotIn('"user_choice": "rock"', seen_turn["system"])
        self.assertNotIn("user:rock", seen_turn["system"])
        sources = {item["kind"]: item for item in payload["prompt_sources"]}
        self.assertEqual(sources["persona"]["persona_id"], persona["id"])
        self.assertEqual(
            sources["user_game"]["fields"],
            ["instructions", "strategy_instructions"],
        )
        self.assertGreaterEqual(sources["memory"]["count"], 1)
        self.assertEqual(
            sources["conversation"]["conversation_id"], conversation["id"]
        )
        self.assertEqual(
            sources["provider"]["provider_id"], conversation["provider_id"]
        )

        result_id = payload["result_id"]
        trusted_result = self.client.get(
            f"/api/casual-games/results/{result_id}"
        )
        self.assertEqual(trusted_result.status_code, 200, trusted_result.text)
        self.assertEqual(
            trusted_result.json()["result"]["notable_events"],
            ["user:rock", "persona:scissors"],
        )

        seen_reply = {}

        async def reply_model(provider, system, prompt, **kwargs):
            seen_reply.update(provider=provider, system=system, prompt=prompt)
            return "这局已经结束了。"

        with patch.object(app_module, "roleplay_model_once", reply_model):
            reply = self.client.post(
                f"/api/casual-games/results/{result_id}/chat-reply"
            )
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["conversation_id"], conversation["id"])
        self.assertEqual(reply.json()["persona_id"], persona["id"])
        reply_prompt = json.loads(seen_reply["prompt"])
        self.assertEqual(
            reply_prompt["casual_game_result"]["notable_events"],
            ["user:rock", "persona:scissors"],
        )
        self.assertIn("rps-reaction-marker", seen_reply["system"])
        with app_module.closing(app_module.db()) as connection:
            stored_reply = connection.execute(
                "SELECT role,conversation_id FROM messages WHERE id=?",
                (reply.json()["assistant_id"],),
            ).fetchone()
        self.assertEqual(stored_reply["role"], "assistant")
        self.assertEqual(stored_reply["conversation_id"], conversation["id"])

        approved = self.client.post(
            f"/api/casual-games/results/{result_id}/memory-decision",
            json={"approved": True, "importance": 0.45},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["provenance"]["source"], "casual_game")
        self.assertEqual(
            approved.json()["provenance"]["reality_scope"], "real_interaction"
        )
        self.assertEqual(
            approved.json()["provenance"]["game_id"], "rock_paper_scissors"
        )
        with app_module.closing(app_module.db()) as connection:
            stored_memory = connection.execute(
                "SELECT * FROM memories WHERE id=?",
                (approved.json()["memory_id"],),
            ).fetchone()
        self.assertEqual(stored_memory["persona_key"], persona["id"])
        self.assertEqual(stored_memory["title"], "与用户完成一局猜拳")
        self.assertEqual(
            stored_memory["content"],
            "我和用户完成了一局猜拳；用户出了石头，我出了剪刀，结果是用户获胜。",
        )

    def test_new_games_use_structured_persona_turns_result_replies_and_memory(self):
        user_secret = "9087"
        bulls_persona, bulls_conversation, bulls_session = self.create_bound_session(
            "bulls_and_cows",
            {"user_secret": user_secret},
        )
        private_bulls = app_module.casual_game_repository().get_session(
            bulls_session["id"]
        )
        persona_secret = private_bulls.state["persona_secret"]
        user_guess = next(
            candidate
            for candidate in ("0123", "1234", "2345", "3456")
            if candidate != persona_secret
        )
        user_turn = self.client.post(
            f"/api/casual-games/sessions/{bulls_session['id']}/actions",
            json={
                "action": {"guess": user_guess},
                "expected_revision": 0,
                "idempotency_key": "bulls-user-guess",
            },
        )
        self.assertEqual(user_turn.status_code, 200, user_turn.text)
        self.assertEqual(user_turn.json()["current_actor"], "persona")
        self.assertNotIn("user_secret", user_turn.json()["state"])
        self.assertNotIn("persona_secret", user_turn.json()["state"])

        seen_bulls_turn = {}

        async def bulls_persona_model(provider, system, prompt, **kwargs):
            seen_bulls_turn.update(system=system, prompt=prompt)
            return json.dumps({"guess": user_secret})

        with patch.object(
            app_module, "roleplay_model_once", bulls_persona_model
        ), patch.object(
            app_module,
            "fallback_ai_game_choice",
            side_effect=AssertionError("fallback must not run"),
        ):
            bulls_turn = self.client.post(
                f"/api/casual-games/sessions/{bulls_session['id']}/persona-turn",
                json={
                    "expected_revision": 1,
                    "idempotency_key": "bulls-persona-guess",
                },
            )
        self.assertEqual(bulls_turn.status_code, 200, bulls_turn.text)
        bulls_payload = bulls_turn.json()
        self.assertEqual(bulls_payload["status"], "finished")
        self.assertEqual(bulls_payload["result"]["outcome"], "persona_win")
        self.assertEqual(len(bulls_payload["state"]["history"]), 2)
        self.assertIn(
            "Return one guess containing exactly four distinct ASCII digits",
            seen_bulls_turn["system"],
        )
        self.assertIn('"action_schema"', seen_bulls_turn["system"])
        self.assertNotIn('"user_secret"', seen_bulls_turn["system"])
        self.assertNotIn('"persona_secret"', seen_bulls_turn["system"])
        self.assertNotIn(user_secret, seen_bulls_turn["system"])

        twenty_persona, twenty_conversation, twenty_session = (
            self.create_bound_session("twenty_questions")
        )
        seen_twenty_turn = {}

        async def twenty_persona_model(provider, system, prompt, **kwargs):
            seen_twenty_turn.update(system=system, prompt=prompt)
            return json.dumps({"kind": "guess", "text": "一只猫"}, ensure_ascii=False)

        with patch.object(
            app_module, "roleplay_model_once", twenty_persona_model
        ), patch.object(
            app_module,
            "fallback_ai_game_choice",
            side_effect=AssertionError("fallback must not run"),
        ):
            twenty_turn = self.client.post(
                f"/api/casual-games/sessions/{twenty_session['id']}/persona-turn",
                json={
                    "expected_revision": 0,
                    "idempotency_key": "twenty-persona-guess",
                },
            )
        self.assertEqual(twenty_turn.status_code, 200, twenty_turn.text)
        self.assertEqual(twenty_turn.json()["current_actor"], "user")
        self.assertEqual(twenty_turn.json()["state"]["question_count"], 1)
        self.assertIn(
            "Return one non-empty question or guess",
            seen_twenty_turn["system"],
        )
        self.assertIn('"kind"', seen_twenty_turn["system"])
        self.assertIn('"guess"', seen_twenty_turn["system"])
        twenty_final = self.client.post(
            f"/api/casual-games/sessions/{twenty_session['id']}/actions",
            json={
                "action": {"verdict": "correct"},
                "expected_revision": 1,
                "idempotency_key": "twenty-user-verdict",
            },
        )
        self.assertEqual(twenty_final.status_code, 200, twenty_final.text)
        self.assertEqual(twenty_final.json()["status"], "finished")
        self.assertEqual(
            twenty_final.json()["result"]["outcome"], "persona_win"
        )

        result_cases = (
            (
                "bulls_and_cows",
                bulls_payload["result_id"],
                bulls_persona,
                bulls_conversation,
                "与用户完成一局猜数字",
                "共进行了2次猜测，结果是我获胜。",
            ),
            (
                "twenty_questions",
                twenty_final.json()["result_id"],
                twenty_persona,
                twenty_conversation,
                "与用户完成一局二十问",
                "在第1问结束，结果是我获胜。",
            ),
        )
        for game_id, result_id, persona, conversation, title, content_fragment in result_cases:
            with self.subTest(game_id=game_id):
                seen_reply = {}

                async def reply_model(provider, system, prompt, **kwargs):
                    seen_reply.update(system=system, prompt=prompt)
                    return "这局已经结束了。"

                with patch.object(app_module, "roleplay_model_once", reply_model):
                    reply = self.client.post(
                        f"/api/casual-games/results/{result_id}/chat-reply"
                    )
                self.assertEqual(reply.status_code, 200, reply.text)
                self.assertEqual(reply.json()["persona_id"], persona["id"])
                self.assertEqual(
                    reply.json()["conversation_id"], conversation["id"]
                )
                reply_prompt = json.loads(seen_reply["prompt"])
                self.assertEqual(
                    reply_prompt["casual_game_result"]["outcome"],
                    "persona_win",
                )

                approved = self.client.post(
                    f"/api/casual-games/results/{result_id}/memory-decision",
                    json={"approved": True},
                )
                self.assertEqual(approved.status_code, 200, approved.text)
                self.assertEqual(
                    approved.json()["provenance"]["game_id"], game_id
                )
                with app_module.closing(app_module.db()) as connection:
                    stored_memory = connection.execute(
                        "SELECT persona_key,title,content FROM memories WHERE id=?",
                        (approved.json()["memory_id"],),
                    ).fetchone()
                self.assertEqual(stored_memory["persona_key"], persona["id"])
                self.assertEqual(stored_memory["title"], title)
                self.assertIn(content_fragment, stored_memory["content"])
                self.assertNotIn(user_secret, stored_memory["content"])
                self.assertNotIn(persona_secret, stored_memory["content"])

    def test_result_reply_is_assistant_only_and_idempotent(self):
        _, conversation, session = self.create_bound_session()
        final = self.finish_user_win(session)
        with app_module.closing(app_module.db()) as connection:
            before_users = connection.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE conversation_id=? AND role='user'",
                (conversation["id"],),
            ).fetchone()["count"]

        async def reply_model(provider, system, prompt, **kwargs):
            self.assertIn("casual_game_result", prompt)
            self.assertIn("user-owned persona text", system)
            return "这局已经结束了。"

        with patch.object(app_module, "roleplay_model_once", reply_model):
            first = self.client.post(
                f"/api/casual-games/results/{final['result_id']}/chat-reply"
            )
        self.assertEqual(first.status_code, 200, first.text)
        with patch.object(
            app_module,
            "roleplay_model_once",
            side_effect=AssertionError("reply retry must not call the model"),
        ):
            replay = self.client.post(
                f"/api/casual-games/results/{final['result_id']}/chat-reply"
            )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["assistant_id"], first.json()["assistant_id"])
        self.assertTrue(replay.json()["idempotent_replay"])
        with app_module.closing(app_module.db()) as connection:
            users = connection.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE conversation_id=? AND role='user'",
                (conversation["id"],),
            ).fetchone()["count"]
            assistants = connection.execute(
                "SELECT * FROM messages WHERE id=?",
                (first.json()["assistant_id"],),
            ).fetchone()
            events = json.loads(
                connection.execute(
                    "SELECT events_json FROM message_tool_events WHERE message_id=?",
                    (first.json()["assistant_id"],),
                ).fetchone()["events_json"]
            )
        self.assertEqual(users, before_users)
        self.assertEqual(assistants["role"], "assistant")
        self.assertIsNone(assistants["parent_message_id"])
        self.assertEqual(events[0]["result_id"], final["result_id"])

    def test_memory_decision_requires_approval_and_records_real_provenance(self):
        persona, conversation, approved_session = self.create_bound_session()
        approved_result = self.finish_user_win(approved_session)
        approved = self.client.post(
            f"/api/casual-games/results/{approved_result['result_id']}/memory-decision",
            json={"approved": True, "importance": 0.5},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        replay = self.client.post(
            f"/api/casual-games/results/{approved_result['result_id']}/memory-decision",
            json={"approved": True, "importance": 0.5},
        )
        self.assertTrue(replay.json()["idempotent_replay"])
        memory_id = approved.json()["memory_id"]
        with app_module.closing(app_module.db()) as connection:
            memory = connection.execute(
                "SELECT * FROM memories WHERE id=?",
                (memory_id,),
            ).fetchone()
            audit = connection.execute(
                """
                SELECT detail FROM memory_audit
                WHERE memory_id=? AND action='casual_game_provenance'
                """,
                (memory_id,),
            ).fetchone()
        self.assertEqual(memory["persona_key"], persona["id"])
        self.assertEqual(memory["source_conversation_id"], conversation["id"])
        self.assertIsNone(memory["source_message_id"])
        self.assertEqual(memory["kind"], "event")
        provenance = json.loads(audit["detail"])
        self.assertEqual(provenance["source"], "casual_game")
        self.assertEqual(provenance["reality_scope"], "real_interaction")
        self.assertEqual(provenance["approved_by"], "user")

        auto_persona, _, auto_session = self.create_bound_session()
        configured = self.client.put(
            f"/api/casual-games/behavior-configs/{auto_persona['id']}/tic_tac_toe",
            json={"memory_mode": "auto"},
        )
        self.assertEqual(configured.status_code, 200, configured.text)
        auto_result = self.finish_user_win(auto_session)
        auto_approved = self.client.post(
            f"/api/casual-games/results/{auto_result['result_id']}/memory-decision",
            json={"approved": True},
        )
        self.assertEqual(auto_approved.status_code, 200, auto_approved.text)
        with app_module.closing(app_module.db()) as connection:
            auto_audit = connection.execute(
                """
                SELECT detail FROM memory_audit
                WHERE memory_id=? AND action='casual_game_provenance'
                """,
                (auto_approved.json()["memory_id"],),
            ).fetchone()
        self.assertEqual(json.loads(auto_audit["detail"])["approved_by"], "user_setting")

        _, _, declined_session = self.create_bound_session()
        declined_result = self.finish_user_win(declined_session)
        with app_module.closing(app_module.db()) as connection:
            before = connection.execute(
                "SELECT COUNT(*) AS count FROM memories"
            ).fetchone()["count"]
        declined = self.client.post(
            f"/api/casual-games/results/{declined_result['result_id']}/memory-decision",
            json={"approved": False},
        )
        self.assertEqual(declined.status_code, 200, declined.text)
        with app_module.closing(app_module.db()) as connection:
            after = connection.execute(
                "SELECT COUNT(*) AS count FROM memories"
            ).fetchone()["count"]
            decision = connection.execute(
                "SELECT memory_decision,memory_id FROM casual_game_results WHERE id=?",
                (declined_result["result_id"],),
            ).fetchone()
        self.assertEqual(after, before)
        self.assertEqual(decision["memory_decision"], "declined")
        self.assertIsNone(decision["memory_id"])

    def test_restore_accepts_v1_casual_result_rows_without_delivery_fields(self):
        _, _, session = self.create_bound_session()
        final = self.finish_user_win(session)
        bundle = self.client.post(
            "/api/backup/export",
            json={"parts": ["games"]},
        ).json()
        for row in bundle["tables"]["casual_game_results"]:
            row.pop("chat_message_id", None)
            row.pop("memory_id", None)
            row.pop("memory_decision", None)
        restored = self.client.post(
            "/api/backup/restore",
            json={"bundle": bundle, "parts": ["games"]},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        loaded = self.client.get(
            f"/api/casual-games/results/{final['result_id']}"
        )
        self.assertEqual(loaded.status_code, 200, loaded.text)
        self.assertIsNone(loaded.json()["chat_message_id"])
        self.assertIsNone(loaded.json()["memory_id"])
        self.assertIsNone(loaded.json()["memory_decision"])

    def test_backup_includes_casual_tables_and_legacy_bundle_preserves_them(self):
        _, _, session = self.create_bound_session()
        played = self.client.post(
            f"/api/casual-games/sessions/{session['id']}/actions",
            json={
                "action": {"position": 0},
                "expected_revision": 0,
                "idempotency_key": "backup-user-turn",
            },
        )
        self.assertEqual(played.status_code, 200, played.text)
        portable_export = self.client.post(
            "/api/backup/export",
            json={"parts": ["personas", "conversations", "games"]},
        )
        self.assertEqual(portable_export.status_code, 200, portable_export.text)
        portable_bundle = portable_export.json()
        exported = self.client.post(
            "/api/backup/export",
            json={"parts": ["games"]},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        bundle = exported.json()
        self.assertTrue(
            set(app_module.CASUAL_GAME_BACKUP_TABLES).issubset(bundle["tables"])
        )

        partial = json.loads(json.dumps(bundle))
        partial["tables"].pop("casual_game_history")
        rejected = self.client.post(
            "/api/backup/restore",
            json={"bundle": partial, "parts": ["games"]},
        )
        self.assertEqual(rejected.status_code, 400)

        orphaned = json.loads(json.dumps(bundle))
        orphaned["tables"]["casual_game_history"][0]["session_id"] = "missing-session"
        orphaned_restore = self.client.post(
            "/api/backup/restore",
            json={"bundle": orphaned, "parts": ["games"]},
        )
        self.assertEqual(orphaned_restore.status_code, 400, orphaned_restore.text)
        self.assertIn("表关系无效", orphaned_restore.json()["detail"])
        preserved_after_failure = self.client.get(
            f"/api/casual-games/sessions/{session['id']}/history"
        ).json()["history"]
        self.assertEqual(len(preserved_after_failure), 1)

        legacy = json.loads(json.dumps(bundle))
        for table in app_module.CASUAL_GAME_BACKUP_TABLES:
            legacy["tables"].pop(table, None)
        restored = self.client.post(
            "/api/backup/restore",
            json={"bundle": legacy, "parts": ["games"]},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        preserved = self.client.get(
            f"/api/casual-games/sessions/{session['id']}"
        )
        self.assertEqual(preserved.status_code, 200, preserved.text)

        missing_binding = json.loads(json.dumps(portable_bundle))
        missing_binding["tables"]["conversations"] = []
        binding_restore = self.client.post(
            "/api/backup/restore",
            json={"bundle": missing_binding, "parts": ["conversations"]},
        )
        self.assertEqual(binding_restore.status_code, 400, binding_restore.text)
        self.assertIn("Conversation 与 Persona", binding_restore.json()["detail"])
        self.assertEqual(
            self.client.get(f"/api/casual-games/sessions/{session['id']}").status_code,
            200,
        )

        app_module.DB_PATH = Path(self.tempdir.name) / "fresh-target.db"
        app_module.init_db()
        app_module.init_game_schema()
        app_module.init_casual_game_schema()
        games_only = self.client.post(
            "/api/backup/restore",
            json={"bundle": portable_bundle, "parts": ["games"]},
        )
        self.assertEqual(games_only.status_code, 400, games_only.text)
        self.assertIn("Conversation 与 Persona", games_only.json()["detail"])

        complete = self.client.post(
            "/api/backup/restore",
            json={
                "bundle": portable_bundle,
                "parts": ["personas", "conversations", "games"],
            },
        )
        self.assertEqual(complete.status_code, 200, complete.text)
        restored_session = self.client.get(
            f"/api/casual-games/sessions/{session['id']}"
        )
        self.assertEqual(restored_session.status_code, 200, restored_session.text)

    def test_backup_export_reads_session_and_history_from_one_snapshot(self):
        _, _, session = self.create_bound_session()
        export_paused = threading.Event()
        release_export = threading.Event()
        writer_started = threading.Event()
        exported: list[dict] = []
        export_errors: list[BaseException] = []
        writer_responses = []
        original_sanitizer = app_module.sanitized_backup_row

        def pausing_sanitizer(table, source):
            if table == "casual_game_sessions" and not export_paused.is_set():
                export_paused.set()
                if not release_export.wait(5):
                    raise RuntimeError("timed out waiting to resume backup export")
            return original_sanitizer(table, source)

        def run_export():
            try:
                exported.append(
                    app_module.export_backup(app_module.BackupExportIn(parts=["games"]))
                )
            except BaseException as error:
                export_errors.append(error)

        def run_writer():
            writer_started.set()
            writer_responses.append(
                self.client.post(
                    f"/api/casual-games/sessions/{session['id']}/actions",
                    json={
                        "action": {"position": 0},
                        "expected_revision": 0,
                        "idempotency_key": "concurrent-backup-turn",
                    },
                )
            )

        with patch.object(app_module, "sanitized_backup_row", pausing_sanitizer):
            export_thread = threading.Thread(target=run_export)
            export_thread.start()
            self.assertTrue(export_paused.wait(5))
            writer_thread = threading.Thread(target=run_writer)
            writer_thread.start()
            self.assertTrue(writer_started.wait(5))
            release_export.set()
            export_thread.join(10)
            writer_thread.join(10)

        self.assertFalse(export_thread.is_alive())
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(export_errors, [])
        self.assertEqual(writer_responses[0].status_code, 200, writer_responses[0].text)
        bundle = exported[0]
        exported_session = next(
            row
            for row in bundle["tables"]["casual_game_sessions"]
            if row["id"] == session["id"]
        )
        self.assertEqual(exported_session["revision"], 0)
        self.assertEqual(bundle["tables"]["casual_game_history"], [])
        current = self.client.get(
            f"/api/casual-games/sessions/{session['id']}"
        ).json()
        self.assertEqual(current["revision"], 1)


if __name__ == "__main__":
    unittest.main()
