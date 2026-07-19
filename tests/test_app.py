import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as app_module


class LocalClientTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db = app_module.DB_PATH
        app_module.DB_PATH = Path(self.tempdir.name) / "test.db"
        app_module.init_db()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        app_module.DB_PATH = self.old_db
        self.tempdir.cleanup()

    def test_bootstrap_starts_with_clean_persona_library(self):
        payload = self.client.get("/api/bootstrap").json()
        self.assertEqual(payload["personas"], [])

    def test_model_ids_are_normalized_and_deduplicated(self):
        payload = {"data": [{"id": "glm-5"}, {"id": "deepseek-chat"}, {"id": "glm-5"}, "custom-model", {}]}
        self.assertEqual(app_module.extract_model_ids(payload), ["custom-model", "deepseek-chat", "glm-5"])

    def test_provider_is_saved_but_key_is_masked(self):
        response = self.client.post("/api/providers", json={
            "name": "测试反代", "protocol": "openai",
            "base_url": "https://proxy.example/v1/", "api_key": "secret",
            "model": "test-model", "custom_headers": "{}", "prompt_cache": True,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["has_api_key"])
        self.assertNotIn("api_key", payload)

    def test_provider_endpoint_avoids_duplicate_v1(self):
        self.assertEqual(app_module.provider_endpoint("https://api.anthropic.com", "anthropic"), "https://api.anthropic.com/v1/messages")
        self.assertEqual(app_module.provider_endpoint("https://proxy.example/v1", "anthropic"), "https://proxy.example/v1/messages")

    def test_conversation_can_be_renamed_and_searched(self):
        created = self.client.post("/api/conversations", json={"title": "新对话"}).json()
        renamed = self.client.patch(f"/api/conversations/{created['id']}", json={"title": "旅行计划"})
        self.assertEqual(renamed.json()["title"], "旅行计划")
        results = self.client.get("/api/search", params={"q": "旅行"}).json()
        self.assertEqual(results[0]["id"], created["id"])

    def test_auto_title_setting_and_local_title(self):
        saved = self.client.put("/api/settings", json={"auto_title_mode": "model"}).json()
        self.assertEqual(saved["auto_title_mode"], "model")
        self.assertEqual(app_module.local_title("  帮我研究一下自动命名。 "), "帮我研究一下自动命名")

    def test_appearance_settings_are_persisted(self):
        saved = self.client.put("/api/settings", json={
            "font_scale": 115,
            "message_density": "relaxed",
            "code_theme": "contrast",
        }).json()
        self.assertEqual(saved["font_scale"], 115)
        self.assertEqual(saved["message_density"], "relaxed")
        self.assertEqual(saved["code_theme"], "contrast")
        loaded = self.client.get("/api/bootstrap").json()["settings"]
        self.assertEqual(loaded["font_scale"], 115)

    def test_conversation_can_be_pinned_starred_and_archived(self):
        created = self.client.post("/api/conversations", json={"title": "测试会话"}).json()
        saved = self.client.patch(
            f"/api/conversations/{created['id']}/state",
            json={"pinned": True, "starred": True, "archived": True},
        ).json()
        self.assertEqual((saved["pinned"], saved["starred"], saved["archived"]), (1, 1, 1))

    def test_deepseek_and_glm_are_auto_identified(self):
        deepseek = self.client.post("/api/providers", json={"name": "线路一", "protocol": "openai", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}).json()
        glm = self.client.post("/api/providers", json={"name": "线路二", "protocol": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.2"}).json()
        self.assertEqual(deepseek["protocol"], "deepseek")
        self.assertEqual(glm["protocol"], "glm")

    def test_memory_can_be_retrieved_and_moved_to_trash(self):
        memory = self.client.post("/api/memories", json={"title": "早餐偏好", "content": "用户早餐喜欢喝热牛奶", "kind": "preference"}).json()
        with app_module.closing(app_module.db()) as connection:
            results = app_module.retrieve_memories(connection, "早餐喝什么")
        self.assertEqual(results[0]["id"], memory["id"])
        trashed = self.client.patch(f"/api/memories/{memory['id']}/state", json={"trash": True}).json()
        self.assertTrue(trashed["trashed"])

    def test_high_frequency_entity_does_not_drown_the_topic(self):
        topics = ["健身操", "戒指", "早餐", "旅行", "天气", "电影", "咖啡", "散步", "工作", "游戏"]
        for index in range(30):
            topic = topics[index % len(topics)]
            self.client.post("/api/memories", json={"title": f"小A与{topic}{index}", "content": f"小A谈到了{topic}的一段普通记录", "kind": "event"})
        relevant = self.client.post("/api/memories", json={"title": "小A写诗", "content": "小A担心写出来不够好，所以修改了三遍那首诗", "kind": "emotion"}).json()
        with app_module.closing(app_module.db()) as connection:
            results = app_module.retrieve_memories(connection, "小A为什么不主动写那首诗")
            broad = app_module.retrieve_memories(connection, "小A")
        self.assertEqual(results[0]["id"], relevant["id"])
        self.assertLessEqual(len(broad), 6)
        self.assertIn("reason", results[0])

    def test_provider_headers_keep_keys_server_side(self):
        anthropic = app_module.provider_headers("anthropic", "secret", '{"X-Test":"yes"}')
        openai = app_module.provider_headers("openai", "secret")
        self.assertEqual(anthropic["x-api-key"], "secret")
        self.assertEqual(anthropic["X-Test"], "yes")
        self.assertEqual(openai["Authorization"], "Bearer secret")

    def test_motivation_state_is_isolated_per_persona(self):
        persona = self.client.post("/api/personas", json={"name": "测试人格", "prompt": "保持诚实"}).json()
        key = persona["id"]
        enabled = self.client.put(f"/api/motivation/{key}/enabled", json={"enabled": True}).json()
        self.assertTrue(enabled["enabled"])
        changed = self.client.post(f"/api/motivation/{key}/event", json={"event": "happy_moment"}).json()
        self.assertGreater(changed["state"]["drives"]["joy"], 35)
        default_state = self.client.get("/api/motivation/__default__").json()
        self.assertFalse(default_state["enabled"])
        self.assertEqual(default_state["state"]["drives"]["joy"], 35)

    def test_motivation_rejects_unknown_event_names(self):
        response = self.client.post("/api/motivation/__default__/event", json={"event": "unknown_legacy_event"})
        self.assertEqual(response.status_code, 422)

    def test_original_fishing_game_has_isolated_persistent_saves(self):
        catalog = self.client.get("/api/games").json()
        self.assertEqual(catalog[0]["id"], "quiet_fishing")
        cast = self.client.post("/api/games/quiet_fishing/action", json={"action": "cast", "amount": 2}).json()
        self.assertEqual(cast["state"]["turn"], 2)
        self.assertEqual(cast["state"]["bait"], 6)
        loaded = self.client.get("/api/games/quiet_fishing/state").json()
        self.assertEqual(loaded["state"]["turn"], 2)
        other = self.client.get("/api/games/quiet_fishing/state", params={"persona_id": "another-persona"}).json()
        self.assertEqual(other["state"]["turn"], 0)

    def test_message_favorite_keeps_a_server_snapshot(self):
        conversation = self.client.post("/api/conversations", json={"title": "值得留下"}).json()
        message_id = str(uuid.uuid4())
        with app_module.closing(app_module.db()) as connection:
            connection.execute("INSERT INTO messages VALUES(?, ?, 'user', ?, NULL, NULL, ?, '', NULL)", (message_id, conversation["id"], "这一句话要留下来", app_module.now_iso()))
            connection.commit()
        saved = self.client.post(f"/api/favorites/{message_id}", json={"owner": "user"})
        self.assertEqual(saved.status_code, 200)
        favorite = self.client.get("/api/favorites").json()[0]
        self.assertEqual(favorite["text_snapshot"], "这一句话要留下来")
        self.assertEqual(favorite["conversation_title_snapshot"], "值得留下")
        self.assertEqual(favorite["owners"], ["user"])

    def test_claw_and_slots_are_playable_and_persistent(self):
        claw = self.client.post("/api/games/claw_machine/action", json={"action": "grab"}).json()
        self.assertEqual(claw["state"]["coins"], 90)
        self.assertEqual(self.client.get("/api/games/claw_machine/state").json()["state"]["turn"], 1)
        slots = self.client.post("/api/games/cloud_slots/action", json={"action": "spin", "amount": 1}).json()
        self.assertEqual(slots["state"]["turn"], 1)
        self.assertEqual(len(slots["state"]["reels"]), 3)

    def test_ai_game_choices_are_whitelisted_and_budgeted(self):
        choice, comment = app_module.parse_ai_game_choice('{"action":"grab","amount":9,"comment":"试试中间"}', "claw_machine")
        self.assertEqual(choice, {"action": "grab", "amount": 1, "target": ""})
        self.assertEqual(comment, "试试中间")
        self.assertEqual(app_module.game_action_cost("claw_machine", choice, {}), 10)
        with self.assertRaises(Exception):
            app_module.parse_ai_game_choice('{"action":"delete_save"}', "claw_machine")

    def test_device_local_time_is_injected_into_chat_context(self):
        provider = self.client.post("/api/providers", json={"name": "时间测试", "protocol": "openai", "base_url": "https://example.com/v1", "api_key": "test", "model": "test-model"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id": provider["id"]}).json()
        body = app_module.ChatIn(conversation_id=conversation["id"], content="现在几点", provider_id=provider["id"], local_time="2026年7月19日 星期日 17:30:00 GMT+8")
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("2026年7月19日", messages[0]["content"])
        self.assertIn("由用户设备提供", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
