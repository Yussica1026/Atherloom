import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as app_module


LEGACY_CATALOG = [
    {
        "id": "homestead",
        "name": "云芽庭院",
        "icon": "▧",
        "status": "playable",
        "description": "种花、养宠物，也可以授权当前人格照料。",
    },
    {
        "id": "nowhere",
        "name": "乌有乡",
        "icon": "◎",
        "status": "playable",
        "description": "原版真实地球旅行；让 AI 用身体在世界上走一走。",
    },
    {
        "id": "quiet_fishing",
        "name": "云汀钓记",
        "icon": "◌",
        "status": "playable",
        "description": "为 AI 与用户共同设计的原创确定性钓鱼游戏。",
    },
    {
        "id": "claw_machine",
        "name": "抓娃娃机",
        "icon": "◇",
        "status": "playable",
        "description": "移动爪子、选择目标并收集娃娃。",
    },
    {
        "id": "cloud_slots",
        "name": "云纹老虎机",
        "icon": "✦",
        "status": "playable",
        "description": "只使用游戏内云贝的确定性三轴小游戏。",
    },
    {
        "id": "star_merge",
        "name": "星潮合成",
        "icon": "▦",
        "status": "playable",
        "description": "你亲手合成星块，或把棋盘交给当前人格。",
    },
    {
        "id": "mist_maze",
        "name": "雾径迷宫",
        "icon": "⌁",
        "status": "playable",
        "description": "你与人格轮流探路，在有限视野里找到出口。",
    },
    {
        "id": "ember_dungeon",
        "name": "余烬地牢",
        "icon": "⚔",
        "status": "playable",
        "description": "探索、迎战与休整都由 Atherloom 判定的轻量冒险。",
    },
]


class LegacyGamesContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = app_module.DB_PATH
        app_module.DB_PATH = Path(self.tempdir.name) / "legacy-contract.db"
        app_module.init_db()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        app_module.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def test_routes_and_request_models_remain_unchanged(self):
        openapi = app_module.app.openapi()
        paths = openapi["paths"]
        expected_methods = {
            "/api/games": {"get"},
            "/api/games/{game_id}/state": {"get"},
            "/api/games/{game_id}/action": {"post"},
            "/api/games/{game_id}/ai-turn": {"post"},
            "/api/games/{game_id}/room-chat": {"post"},
        }
        for path, methods in expected_methods.items():
            self.assertIn(path, paths)
            actual_methods = {key for key in paths[path] if key in {"get", "post", "put", "patch", "delete"}}
            self.assertEqual(actual_methods, methods)

        schemas = openapi["components"]["schemas"]
        action = schemas["GameActionIn"]
        self.assertEqual(action["required"], ["action"])
        self.assertEqual(set(action["properties"]), {"action", "amount", "target"})
        self.assertEqual(action["properties"]["amount"]["default"], 1)
        self.assertEqual(action["properties"]["amount"]["minimum"], 1)
        self.assertEqual(action["properties"]["amount"]["maximum"], 20)
        self.assertEqual(action["properties"]["target"]["default"], "")

        ai_turn = schemas["AiGameTurnIn"]
        self.assertEqual(ai_turn["required"], ["provider_id"])
        self.assertEqual(
            set(ai_turn["properties"]),
            {"provider_id", "persona_id", "turns", "autonomous", "max_spend"},
        )
        self.assertEqual(ai_turn["properties"]["turns"]["default"], 1)
        self.assertEqual(ai_turn["properties"]["turns"]["maximum"], 9)
        self.assertEqual(ai_turn["properties"]["autonomous"]["default"], False)
        self.assertEqual(ai_turn["properties"]["max_spend"]["default"], 30)
        self.assertEqual(ai_turn["properties"]["max_spend"]["maximum"], 100)

        room_chat = schemas["GameRoomChatIn"]
        self.assertEqual(room_chat["required"], ["provider_id", "content"])
        self.assertEqual(set(room_chat["properties"]), {"provider_id", "persona_id", "content"})
        self.assertEqual(room_chat["properties"]["content"]["minLength"], 1)
        self.assertEqual(room_chat["properties"]["content"]["maxLength"], 2000)

    def test_catalog_and_basic_response_shapes_are_frozen(self):
        catalog = self.client.get("/api/games")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertEqual(catalog.json(), LEGACY_CATALOG)

        initial = self.client.get("/api/games/quiet_fishing/state")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(set(initial.json()), {"game_id", "state", "waters"})

        action = self.client.post(
            "/api/games/quiet_fishing/action",
            json={"action": "cast", "amount": 1},
        )
        self.assertEqual(action.status_code, 200, action.text)
        self.assertEqual(set(action.json()), {"state", "events"})
        self.assertEqual(action.json()["state"]["turn"], 1)

        missing = self.client.get("/api/games/not-a-game/state")
        self.assertEqual(missing.status_code, 404)

    def test_game_saves_schema_and_existing_rows_survive_casual_schema_init(self):
        with closing(sqlite3.connect(app_module.DB_PATH)) as connection:
            table_info = connection.execute("PRAGMA table_info(game_saves)").fetchall()
            self.assertEqual(
                [(row[1], row[2], row[3], row[5]) for row in table_info],
                [
                    ("game_id", "TEXT", 1, 1),
                    ("persona_key", "TEXT", 1, 2),
                    ("state_json", "TEXT", 1, 0),
                    ("updated_at", "TEXT", 1, 0),
                ],
            )
            connection.execute(
                "INSERT INTO game_saves(game_id,persona_key,state_json,updated_at) VALUES(?,?,?,?)",
                ("quiet_fishing", "persona-contract", '{"turn":7}', "2026-08-24T00:00:00+00:00"),
            )
            connection.commit()
            before = connection.execute(
                "SELECT game_id,persona_key,state_json,updated_at FROM game_saves ORDER BY game_id,persona_key"
            ).fetchall()

        from backend.casual_games.schema import init_schema

        init_schema(lambda: app_module.DB_PATH)
        with closing(sqlite3.connect(app_module.DB_PATH)) as connection:
            after = connection.execute(
                "SELECT game_id,persona_key,state_json,updated_at FROM game_saves ORDER BY game_id,persona_key"
            ).fetchall()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
