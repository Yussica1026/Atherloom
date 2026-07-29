import asyncio
import tempfile
import unittest
import uuid
import sys
from pathlib import Path
from unittest.mock import patch

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

    def test_database_schema_version_is_recorded_and_future_versions_are_refused(self):
        with app_module.closing(app_module.db()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, app_module.DB_SCHEMA_VERSION)
            connection.execute(f"PRAGMA user_version = {app_module.DB_SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(RuntimeError, "高于当前程序支持"):
            app_module.init_db()

    def test_sse_parser_reassembles_multiline_events_and_flushes_final_event(self):
        async def lines():
            for line in (
                ": keepalive",
                "data: {\"choices\":[{\"delta\":",
                'data: {\"content\":\"你\"}}]}',
                "",
                'data: {"choices":[{"delta":{"content":"好"}}]}',
                'data: {"choices":[{"delta":{"content":"呀"}}]}',
            ):
                yield line

        async def collect():
            return [event async for event in app_module.iter_sse_json(lines())]

        events = asyncio.run(collect())
        self.assertEqual(events[0]["choices"][0]["delta"]["content"], "你")
        self.assertEqual(events[1]["choices"][0]["delta"]["content"], "好")
        self.assertEqual(events[2]["choices"][0]["delta"]["content"], "呀")

    def test_roleplay_story_keeps_independent_drafts_and_exact_checkpoint(self):
        narrator = self.client.post("/api/providers", json={"name":"旁白","protocol":"openai","base_url":"https://example.com/v1","model":"n"}).json()
        actor = self.client.post("/api/providers", json={"name":"角色","protocol":"openai","base_url":"https://example.com/v1","model":"a"}).json()
        story = self.client.post("/api/roleplay/stories", json={
            "title":"雾港来信","player_name":"玩家角色","premise":"雨夜的旧车站",
            "narrator_provider_id":narrator["id"],
            "cast":[{"name":"示例角色","provider_id":actor["id"],"description":"寡言"}],
        }).json()
        async def fake_model(provider, system, prompt):
            return "雨落在旧站台上，示例角色抬眼望来。" if "小说旁白" in system else "示例角色会抬眼，但等玩家先开口。"
        with patch.object(app_module, "roleplay_model_once", side_effect=fake_model):
            turn = self.client.post(f"/api/roleplay/stories/{story['id']}/turns", json={"player_input":"玩家角色推开候车室的门。"}).json()
        self.assertEqual(turn["turn_number"], 1)
        self.assertEqual(turn["actor_drafts"][0]["name"], "示例角色")
        self.assertEqual(turn["checkpoint"]["last_player_input"], "玩家角色推开候车室的门。")
        saved = self.client.get(f"/api/roleplay/stories/{story['id']}").json()
        self.assertEqual(saved["state"]["turn_number"], 1)
        self.assertTrue(saved["state"]["fictional_archive"])
        self.assertEqual(saved["turns"][0]["prose"], turn["prose"])
        favorite = self.client.patch(f"/api/roleplay/stories/{story['id']}/turns/1", json={"favorite":True}).json()
        self.assertTrue(favorite["checkpoint"]["favorite"])
        removed = self.client.delete(f"/api/roleplay/stories/{story['id']}/turns/1").json()
        self.assertTrue(removed["ok"])
        self.assertEqual(removed["state"]["turn_number"], 0)

    def test_roleplay_archive_is_fiction_labeled_for_later_chat(self):
        provider = self.client.post("/api/providers", json={"name":"线路","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        story = self.client.post("/api/roleplay/stories", json={
            "title":"月下书院","player_name":"玩家角色","narrator_provider_id":provider["id"],
            "cast":[{"name":"示例角色","provider_id":provider["id"]}],
        }).json()
        with app_module.closing(app_module.db()) as connection:
            state={"turn_number":7,"scene":"停在藏书阁门前","rolling_summary":"示例角色与玩家找到了旧钥匙。","fictional_archive":True}
            connection.execute("UPDATE roleplay_stories SET state_json=? WHERE id=?", (app_module.json.dumps(state,ensure_ascii=False),story["id"]))
            connection.commit()
            context=app_module.relevant_roleplay_archive(connection,"示例角色在书院玩到哪里了")
        self.assertIn("<fictional_roleplay_archive>",context)
        self.assertIn("精确停在第 7 回合",context)
        self.assertIn("不得当作用户的现实经历",context)

    def test_roleplay_narrator_writes_opening_without_player_action(self):
        provider = self.client.post("/api/providers", json={"name":"旁白","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        book = self.client.post("/api/worldbooks", json={"name":"古风设定","entries":[{"name":"地点","content":"故事发生在临水城。","enabled":True}]}).json()
        story = self.client.post("/api/roleplay/stories", json={
            "title":"旧城","player_name":"玩家角色","preset":"ancient",
            "narrator_provider_id":provider["id"],"worldbook_ids":[book["id"]],
            "cast":[{"name":"示例角色","provider_id":provider["id"]}],
        }).json()
        async def fake_opening(_provider, system, prompt):
            self.assertIn("绝不能替玩家角色行动", system)
            self.assertIn("临水城", prompt)
            return "雨落临水城，示例角色站在檐下，静候门后的人回应。"
        with patch.object(app_module, "roleplay_model_once", side_effect=fake_opening):
            opening = self.client.post(f"/api/roleplay/stories/{story['id']}/opening", json={}).json()
        self.assertEqual(opening["turn_number"], 0)
        self.assertEqual(opening["actor_drafts"], [])
        saved = self.client.get(f"/api/roleplay/stories/{story['id']}").json()
        self.assertEqual(saved["state"]["turn_number"], 0)
        self.assertIn("雨落临水城", saved["state"]["rolling_summary"])

    def test_worldbook_crud_and_selected_instruction_injection(self):
        provider = self.client.post("/api/providers", json={"name":"测试","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"]}).json()
        book = self.client.post("/api/worldbooks", json={"name":"共同规则","description":"跨人格复用","entries":[{"name":"常驻规则","content":"始终使用简洁中文。","constant":True,"position":"system_after"},{"name":"旅行设定","content":"旅行发生在云海城。","keywords":["旅行"],"scan_depth":4,"position":"system_after"}]}).json()
        self.assertEqual(self.client.get("/api/bootstrap").json()["worldbooks"][0]["name"], "共同规则")
        with app_module.closing(app_module.db()) as connection:
            body=app_module.ChatIn(conversation_id=conversation["id"],content="我想去旅行",provider_id=provider["id"],worldbook_ids=[book["id"]])
            _,_,messages=app_module.load_chat_context(connection,body)
            system=messages[0]["content"]
            self.assertIn("始终使用简洁中文",system);self.assertIn("旅行发生在云海城",system)
            body.worldbook_ids=[];_,_,plain=app_module.load_chat_context(connection,body);self.assertNotIn("旅行发生在云海城",plain[0]["content"])

    def test_selected_worldbook_entry_without_trigger_is_injected(self):
        provider = self.client.post("/api/providers", json={"name":"测试","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"]}).json()
        book = self.client.post("/api/worldbooks", json={"name":"世界书","entries":[{"name":"祝福","content":"清风，明月，我","constant":False,"keywords":[]}]}).json()
        with app_module.closing(app_module.db()) as connection:
            body = app_module.ChatIn(conversation_id=conversation["id"], content="你看到了吗", provider_id=provider["id"], worldbook_ids=[book["id"]])
            _, _, messages = app_module.load_chat_context(connection, body)
        self.assertIn("清风，明月，我", messages[0]["content"])

    def test_mcp_server_crud_masks_token_and_can_bind_to_persona(self):
        server = self.client.post("/api/mcp-servers", json={"name":"memory","url":"https://memory.example.com/mcp","token":"secret-token"}).json()
        self.assertTrue(server["has_token"])
        self.assertNotIn("token", server)
        bootstrap = self.client.get("/api/bootstrap").json()
        self.assertEqual(bootstrap["mcp_servers"][0]["name"], "memory")
        self.assertNotIn("secret-token", str(bootstrap))
        persona = self.client.post("/api/personas", json={"name":"朋友","prompt":"保持诚实","config":{"mcp_servers":["memory"]}}).json()
        self.assertEqual(persona["config"]["mcp_servers"], ["memory"])
        updated = self.client.put(f"/api/mcp-servers/{server['id']}", json={"name":"memory","url":"https://memory.example.com/v2/mcp","token":""}).json()
        self.assertTrue(updated["has_token"])
        self.assertEqual(updated["url"], "https://memory.example.com/v2/mcp")
        self.assertEqual(self.client.delete(f"/api/mcp-servers/{server['id']}").json(), {"ok":True})

    def test_stdio_mcp_can_refresh_tools(self):
        fixture = Path(__file__).with_name("fixture_mcp.py")
        server = self.client.post("/api/mcp-servers", json={"name":"local-tools","transport":"stdio","command":sys.executable,"args":[str(fixture)]}).json()
        refreshed = self.client.post(f"/api/mcp-servers/{server['id']}/refresh").json()
        self.assertEqual(refreshed["last_status"], "online")
        self.assertEqual(refreshed["tools"][0]["name"], "echo")

    def test_builtin_tools_follow_permissions_and_mutate_memory_by_id(self):
        tools, bindings = app_module.builtin_tool_catalog({"web_search":"allow","memory_read":"allow","memory_write":"allow"})
        names = {tool["name"] for tool in tools}
        self.assertEqual(names, {"atherloom_game_play", "atherloom_web_search", "atherloom_memory_search", "atherloom_memory_create", "atherloom_memory_update"})
        self.assertEqual(bindings["atherloom_memory_update"][1], "memory_update")
        conversation = self.client.post("/api/conversations", json={"title": "来源测试"}).json()
        source_message_id = "source-message"
        with app_module.closing(app_module.db()) as connection:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'user', ?, NULL, NULL, ?, '', NULL)",
                (source_message_id, conversation["id"], "我喜欢热牛奶", app_module.now_iso()),
            )
            connection.commit()
        created = asyncio.run(app_module.invoke_builtin_tool("memory_create", {
            "title":"饮品", "content":"用户喜欢热牛奶", "kind":"preference",
            "source_message_id": source_message_id,
        }))
        found = asyncio.run(app_module.invoke_builtin_tool("memory_search", {"query":"热牛奶"}))
        self.assertEqual(found["memories"][0]["memory_id"], created["memory_id"])
        saved = self.client.get("/api/memories", params={"q": "热牛奶"}).json()[0]
        self.assertEqual(saved["source_message_id"], source_message_id)
        self.assertEqual(saved["source_conversation_id"], conversation["id"])
        updated = asyncio.run(app_module.invoke_builtin_tool("memory_update", {"memory_id":created["memory_id"],"content":"用户现在喜欢温牛奶"}))
        self.assertTrue(updated["updated"])
        self.assertEqual(self.client.get("/api/memories?q=温牛奶").json()[0]["id"], created["memory_id"])
        denied, _ = app_module.builtin_tool_catalog({"web_search":"deny","memory_read":"ask","memory_write":"deny"})
        self.assertEqual([tool["name"] for tool in denied], ["atherloom_game_play"])
        played = asyncio.run(app_module.invoke_builtin_tool("game_play", {"game_id": "claw_machine"}))
        self.assertEqual(played["game_id"], "claw_machine")
        self.assertEqual(played["executed"]["action"], "grab")
        self.assertEqual(played["state"]["turn"], 1)

    def test_deepseek_dsml_tool_call_is_parsed(self):
        content = (
            '<｜DSML｜tool_calls><｜DSML｜invoke name="atherloom_web_search">'
            '<｜DSML｜parameter name="query" string="true">2026年 猫 趣闻<｜DSML｜/parameter>'
            '<｜DSML｜parameter name="max_results" string="false">5<｜DSML｜/parameter>'
            '<｜DSML｜/invoke><｜DSML｜/tool_calls>'
        )
        calls = app_module.parse_dsml_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "atherloom_web_search")
        self.assertEqual(
            app_module.json.loads(calls[0]["function"]["arguments"]),
            {"query": "2026年 猫 趣闻", "max_results": 5},
        )
        self.assertTrue(calls[0]["_dsml"])

    def test_tool_responses_normalize_for_repeated_openai_rounds(self):
        raw = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "atherloom_memory_search", "arguments": '{"query":"石头狗"}'},
                    }],
                }
            }]
        }
        normalized = app_module.normalized_provider_tool_response(
            raw, "openai", {"atherloom_memory_search": object()}
        )
        self.assertEqual(normalized["calls"][0]["arguments"], {"query": "石头狗"})
        followup = app_module.provider_tool_followup(
            "openai", normalized["raw_assistant"], normalized["calls"],
            [{"content": '{"memories":[]}', "is_error": False}],
        )
        self.assertEqual(followup[1]["tool_call_id"], "call-1")
        self.assertEqual(followup[1]["role"], "tool")

    def test_python_and_standalone_tool_loops_share_hard_budgets(self):
        backend = (app_module.ROOT / "backend" / "app.py").read_text(encoding="utf-8")
        standalone = (app_module.ROOT / "frontend" / "assets" / "standalone.js").read_text(encoding="utf-8")
        self.assertEqual(app_module.MAX_TOOL_ROUNDS, 4)
        self.assertEqual(app_module.MAX_TOOL_CALLS_PER_TURN, 12)
        self.assertEqual(app_module.MAX_TOOL_CALLS_PER_ROUND, 4)
        self.assertIn("for _round in range(MAX_TOOL_ROUNDS)", backend)
        self.assertIn("maxToolRounds=4,maxToolCalls=12,maxCallsPerRound=4", standalone)
        self.assertIn("工具调用预算已用完", backend)
        self.assertIn("工具调用预算已用完", standalone)

    def test_dynamic_runtime_context_stays_after_cacheable_prompt_prefix(self):
        provider = self.client.post("/api/providers", json={"name":"cache","protocol":"anthropic","base_url":"https://api.anthropic.com","model":"m"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"]}).json()
        body = app_module.ChatIn(
            conversation_id=conversation["id"], content="你好", provider_id=provider["id"],
            local_time="2026-07-28 20:00", media_context="歌曲：《测试》\n当前播放点：00:10",
        )
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        system = messages[0]["content"]
        marker = "\n\n<runtime_context>\n"
        self.assertIn(marker, system)
        stable, runtime = system.split(marker, 1)
        self.assertNotIn("2026-07-28 20:00", stable)
        self.assertIn("2026-07-28 20:00", runtime)
        self.assertIn("<shared_listening_evidence>", runtime)
        android = (app_module.ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "atherloom" / "mobile" / "MainActivity.java").read_text(encoding="utf-8")
        standalone = (app_module.ROOT / "frontend" / "assets" / "standalone.js").read_text(encoding="utf-8")
        self.assertIn('marker="\\n\\n<runtime_context>\\n"', android)
        self.assertIn('const marker="\\n\\n<runtime_context>\\n"', standalone)

    def test_one_click_ai_tools_control_exists(self):
        html = (app_module.ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (app_module.ROOT / "frontend" / "assets" / "app.js").read_text(encoding="utf-8")
        standalone = (app_module.ROOT / "frontend" / "assets" / "standalone.js").read_text(encoding="utf-8")
        android = (app_module.ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "atherloom" / "mobile" / "MainActivity.java").read_text(encoding="utf-8")
        self.assertIn('id="enableAiTools"', html)
        self.assertIn('["web_search", "file_read", "memory_read", "memory_write", "diary_write"]', script)
        self.assertIn("atherloom_memory_update", standalone)
        self.assertIn("toolFollowupMessages", standalone)
        self.assertIn("webSearch(String raw)", android)
        self.assertIn('payload.put("tools",tools)', android)
        self.assertIn("parseDsmlToolCalls", android)

    def test_android_camera_uses_image_capture_instead_of_generic_picker(self):
        source = (app_module.ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "atherloom" / "mobile" / "MainActivity.java").read_text(encoding="utf-8")
        self.assertIn("MediaStore.ACTION_IMAGE_CAPTURE", source)
        self.assertIn("MediaStore.EXTRA_OUTPUT", source)
        self.assertIn("pendingCameraUri", source)

    def test_model_ids_are_normalized_and_deduplicated(self):
        payload = {"data": [{"id": "glm-5"}, {"id": "deepseek-chat"}, {"id": "glm-5"}, "custom-model", {}]}
        self.assertEqual(app_module.extract_model_ids(payload), ["custom-model", "deepseek-chat", "glm-5"])

    def test_answer_versions_can_be_selected_and_soft_deleted(self):
        conversation_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
        first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
        with app_module.closing(app_module.db()) as connection:
            connection.execute("INSERT INTO conversations VALUES (?, '版本测试', NULL, NULL, '', ?, ?, 0, 0, 0)", (conversation_id, app_module.now_iso(), app_module.now_iso()))
            connection.execute("INSERT INTO messages VALUES (?, ?, 'user', '你好', NULL, NULL, ?, '', NULL)", (user_id, conversation_id, "2026-07-19T10:00:00"))
            connection.execute("INSERT INTO messages VALUES (?, ?, 'assistant', '第一版', NULL, 'm', ?, '', ?)", (first_id, conversation_id, "2026-07-19T10:00:01", user_id))
            connection.execute("INSERT INTO messages VALUES (?, ?, 'assistant', '第二版', NULL, 'm', ?, '', ?)", (second_id, conversation_id, "2026-07-19T10:00:02", user_id))
            connection.commit()
        selected = self.client.patch("/api/messages/selection", json={"conversation_id": conversation_id, "parent_message_id": user_id, "assistant_message_id": first_id})
        self.assertEqual(selected.status_code, 200)
        rows = self.client.get(f"/api/conversations/{conversation_id}/messages").json()
        self.assertTrue(next(row for row in rows if row["id"] == first_id)["selected"])
        self.assertEqual(self.client.delete(f"/api/messages/{first_id}").status_code, 200)
        remaining = self.client.get(f"/api/conversations/{conversation_id}/messages").json()
        self.assertNotIn(first_id, [row["id"] for row in remaining])
        self.assertIn(second_id, [row["id"] for row in remaining])

    def test_messages_can_be_edited_and_all_answer_versions_deleted(self):
        conversation_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
        first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
        with app_module.closing(app_module.db()) as connection:
            connection.execute("INSERT INTO conversations VALUES (?, '消息操作', NULL, NULL, '包含旧问题的摘要', ?, ?, 0, 0, 0)", (conversation_id, app_module.now_iso(), app_module.now_iso()))
            connection.execute("INSERT INTO messages VALUES (?, ?, 'user', '旧问题', NULL, NULL, ?, '', NULL)", (user_id, conversation_id, "2026-07-22T11:00:00"))
            connection.execute("INSERT INTO messages VALUES (?, ?, 'assistant', '第一版', NULL, 'm', ?, '', ?)", (first_id, conversation_id, "2026-07-22T11:00:01", user_id))
            connection.execute("INSERT INTO messages VALUES (?, ?, 'assistant', '第二版', NULL, 'm', ?, '', ?)", (second_id, conversation_id, "2026-07-22T11:00:02", user_id))
            connection.commit()
        edited = self.client.patch(f"/api/messages/{user_id}", json={"content": "修改后的问题"})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()["content"], "修改后的问题")
        deleted = self.client.delete(f"/api/messages/{first_id}/versions")
        self.assertEqual(set(deleted.json()["deleted"]), {first_id, second_id})
        remaining = self.client.get(f"/api/conversations/{conversation_id}/messages").json()
        self.assertEqual([row["id"] for row in remaining], [user_id])
        with app_module.closing(app_module.db()) as connection:
            summary = connection.execute("SELECT summary FROM conversations WHERE id=?", (conversation_id,)).fetchone()["summary"]
        self.assertEqual(summary, "")

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

    def test_provider_can_be_edited_without_erasing_key(self):
        created = self.client.post("/api/providers", json={"name":"DS","protocol":"deepseek","base_url":"https://api.deepseek.com","api_key":"secret","model":"flash"}).json()
        updated = self.client.put(f"/api/providers/{created['id']}", json={"name":"DS Pro","protocol":"deepseek","base_url":"https://api.deepseek.com","api_key":"","model":"pro","temperature":0.3,"top_p":0.8,"max_tokens":8192,"stream_enabled":False}).json()
        self.assertEqual(updated["model"], "pro")
        self.assertEqual(updated["temperature"], 0.3)
        self.assertEqual(updated["max_tokens"], 8192)
        self.assertTrue(updated["has_api_key"])
        self.assertFalse(updated["stream_enabled"])

    def test_provider_model_copy_reuses_saved_key(self):
        created = self.client.post("/api/providers", json={"name":"DS","protocol":"deepseek","base_url":"https://api.deepseek.com","api_key":"secret","model":"flash"}).json()
        copied = self.client.post("/api/providers", json={"name":"DS Pro","protocol":"deepseek","base_url":"https://api.deepseek.com","api_key":"","model":"pro","source_provider_id":created["id"]}).json()
        self.assertTrue(copied["has_api_key"])
        with app_module.closing(app_module.db()) as connection:
            self.assertEqual(connection.execute("SELECT api_key FROM providers WHERE id=?", (copied["id"],)).fetchone()["api_key"], "secret")

    def test_same_gateway_new_model_reuses_saved_key_without_retyping(self):
        first = self.client.post("/api/providers", json={"name":"DS Flash","protocol":"deepseek","base_url":"https://api.deepseek.com/","api_key":"secret","model":"v4-flash"}).json()
        second = self.client.post("/api/providers", json={"name":"DS Pro","protocol":"deepseek","base_url":"https://api.deepseek.com","api_key":"","model":"v4-pro"}).json()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["model"], "v4-pro")
        self.assertTrue(second["has_api_key"])
        with app_module.closing(app_module.db()) as connection:
            self.assertEqual(connection.execute("SELECT api_key FROM providers WHERE id=?", (second["id"],)).fetchone()["api_key"], "secret")

    def test_provider_model_list_reuses_saved_key(self):
        created = self.client.post("/api/providers", json={"name":"DS","protocol":"deepseek","base_url":"https://api.deepseek.com","api_key":"secret","model":"flash"}).json()
        seen = {}
        class Response:
            status_code = 200
            def json(self): return {"data":[{"id":"v4-pro"},{"id":"v4-flash"}]}
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get(self, url, headers):
                seen.update(headers)
                return Response()
        with patch.object(app_module.httpx, "AsyncClient", Client):
            result = self.client.post("/api/providers/models", json={"provider_id":created["id"],"protocol":"deepseek","base_url":"https://api.deepseek.com","api_key":""})
        self.assertEqual(result.json()["models"], ["v4-flash", "v4-pro"])
        self.assertEqual(seen["Authorization"], "Bearer secret")

    def test_image_attachment_uses_provider_vision_format(self):
        data = "data:image/jpeg;base64,YWJj"
        openai = app_module.attachment_content("看图", [{"kind":"image","mime":"image/jpeg","data":data}], "openai")
        anthropic = app_module.attachment_content("看图", [{"kind":"image","mime":"image/jpeg","data":data}], "anthropic")
        self.assertEqual(openai[1]["image_url"]["url"], data)
        self.assertEqual(anthropic[1]["source"]["data"], "YWJj")

    def test_provider_capabilities_control_images_and_cache(self):
        provider = self.client.post("/api/providers", json={
            "name": "capability", "protocol": "openai", "base_url": "https://example.com/v1",
            "model": "m", "vision_mode": "text", "cache_mode": "openai",
            "prompt_cache_key": "persona-stable",
        }).json()
        self.assertEqual(provider["vision_mode"], "text")
        self.assertEqual(provider["cache_mode"], "openai")
        self.assertEqual(provider["prompt_cache_key"], "persona-stable")
        with self.assertRaises(app_module.HTTPException) as raised:
            app_module.attachment_content("image", [{"kind": "image", "mime": "image/jpeg", "data": "data:image/jpeg;base64,YWJj"}], "openai", "text")
        self.assertEqual(raised.exception.status_code, 422)

    def test_dedicated_vision_provider_handles_image_without_rebinding_chat(self):
        text = self.client.post("/api/providers", json={"name":"DS","protocol":"deepseek","base_url":"https://example.com/v1","model":"ds","vision_mode":"text"}).json()
        vision = self.client.post("/api/providers", json={"name":"Vision","protocol":"openai","base_url":"https://example.com/v1","model":"vision","vision_mode":"openai"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":text["id"]}).json()
        body = app_module.ChatIn(conversation_id=conversation["id"], content="看图", provider_id=text["id"], vision_provider_id=vision["id"], attachments=[{"kind":"image","mime":"image/jpeg","data":"data:image/jpeg;base64,YWJj"}])
        with app_module.closing(app_module.db()) as connection:
            provider, _, _ = app_module.load_chat_context(connection, body)
        self.assertEqual(provider["id"], vision["id"])
        current = next(item for item in self.client.get("/api/bootstrap").json()["conversations"] if item["id"] == conversation["id"])
        self.assertEqual(current["provider_id"], text["id"])

    def test_reroll_context_contains_original_user_message_once(self):
        provider = self.client.post("/api/providers", json={"name":"测试","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"]}).json()
        user_id = str(uuid.uuid4())
        with app_module.closing(app_module.db()) as connection:
            connection.execute("INSERT INTO messages VALUES(?, ?, 'user', ?, ?, ?, ?, '', NULL)", (user_id, conversation["id"], "不要重复我", provider["id"], "m", app_module.now_iso()))
            connection.commit()
            body = app_module.ChatIn(conversation_id=conversation["id"], content="不要重复我", provider_id=provider["id"], reuse_user_message_id=user_id)
            _, _, messages = app_module.load_chat_context(connection, body, connection.execute("SELECT created_at FROM messages WHERE id=?", (user_id,)).fetchone()["created_at"])
        app_module.append_pending_user(messages, body)
        self.assertEqual([item["content"] for item in messages if item["role"] == "user"].count("不要重复我"), 1)

    def test_shared_watch_evidence_is_hidden_system_context(self):
        provider = self.client.post("/api/providers", json={"name":"watch","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"]}).json()
        body = app_module.ChatIn(
            conversation_id=conversation["id"], content="这一幕是什么意思？",
            provider_id=provider["id"], media_context="影片：测试片\n当前播放点：00:05\n[00:04] 门关上了",
        )
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        system = "\n".join(str(item["content"]) for item in messages if item["role"] == "system")
        self.assertIn("<shared_watch_evidence>", system)
        self.assertIn("门关上了", system)
        self.assertIn("不要剧透", system)

    def test_shared_reading_evidence_is_hidden_and_scoped(self):
        provider = self.client.post("/api/providers", json={"name":"reader","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"]}).json()
        body = app_module.ChatIn(
            conversation_id=conversation["id"], content="这一段是什么意思？",
            provider_id=provider["id"], media_context="书籍：测试书\n本地阅读位置：约 20%\n阅读片段：门关上了",
        )
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        system = "\n".join(str(item["content"]) for item in messages if item["role"] == "system")
        self.assertIn("<shared_reading_evidence>", system)
        self.assertIn("门关上了", system)
        self.assertIn("不要假装读过未提供的正文", system)
        self.assertNotIn("<shared_watch_evidence>", system)

    def test_shared_listening_evidence_is_hidden_and_scoped(self):
        provider = self.client.post("/api/providers", json={"name":"listener","protocol":"openai","base_url":"https://example.com/v1","model":"m"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"]}).json()
        body = app_module.ChatIn(
            conversation_id=conversation["id"], content="你听到这里是什么感觉？",
            provider_id=provider["id"], media_context="歌曲：《晚风》\n当前播放点：01:20\n[01:18] 风吹过旧站台",
        )
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        system = "\n".join(str(item["content"]) for item in messages if item["role"] == "system")
        self.assertIn("<shared_listening_evidence>", system)
        self.assertIn("风吹过旧站台", system)
        self.assertIn("不得编造歌词", system)
        self.assertNotIn("<shared_watch_evidence>", system)

    def test_provider_endpoint_avoids_duplicate_v1(self):
        self.assertEqual(app_module.provider_endpoint("https://api.anthropic.com", "anthropic"), "https://api.anthropic.com/v1/messages")
        self.assertEqual(app_module.provider_endpoint("https://proxy.example/v1", "anthropic"), "https://proxy.example/v1/messages")

    def test_conversation_can_be_renamed_and_searched(self):
        created = self.client.post("/api/conversations", json={"title": "新对话"}).json()
        renamed = self.client.patch(f"/api/conversations/{created['id']}", json={"title": "旅行计划"})
        self.assertEqual(renamed.json()["title"], "旅行计划")
        results = self.client.get("/api/search", params={"q": "旅行"}).json()
        self.assertEqual(results[0]["id"], created["id"])

    def test_messages_can_be_searched_by_body_and_role(self):
        conversation = self.client.post("/api/conversations", json={"title": "旧日谈话"}).json()
        created = app_module.now_iso()
        with app_module.closing(app_module.db()) as connection:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'user', ?, NULL, NULL, ?, '', NULL)",
                ("message-user", conversation["id"], "枔枔问起桂花", created),
            )
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'assistant', ?, NULL, NULL, ?, '', ?)",
                ("message-assistant", conversation["id"], "C 说桂花落在窗边", created, "message-user"),
            )
            connection.commit()
        results = self.client.get("/api/messages/search", params={"q": "桂花", "role": "assistant"}).json()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "message-assistant")
        self.assertEqual(results[0]["conversation_title"], "旧日谈话")
        self.assertEqual(self.client.get("/api/messages/search", params={"q": "桂花", "role": "invalid"}).status_code, 422)

    def test_mcp_audit_links_to_triggering_message(self):
        conversation = self.client.post("/api/conversations", json={"title": "审计会话"}).json()
        with app_module.closing(app_module.db()) as connection:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'user', ?, NULL, NULL, ?, '', NULL)",
                ("audit-user-message", conversation["id"], "请查一下真实资料", app_module.now_iso()),
            )
            connection.commit()
        app_module.record_mcp_audit(
            "__builtin__", "web_search", "success",
            conversation_id=conversation["id"], user_message_id="audit-user-message",
        )
        row = self.client.get("/api/mcp-audit").json()[0]
        self.assertEqual(row["conversation_id"], conversation["id"])
        self.assertEqual(row["user_message_id"], "audit-user-message")
        self.assertEqual(row["conversation_title"], "审计会话")
        self.assertEqual(row["user_message_content"], "请查一下真实资料")

    def test_auto_title_setting_and_local_title(self):
        saved = self.client.put("/api/settings", json={"auto_title_mode": "model"}).json()
        self.assertEqual(saved["auto_title_mode"], "model")
        self.assertEqual(app_module.local_title("  帮我研究一下自动命名。 "), "帮我研究一下自动命名")

    def test_appearance_settings_are_persisted(self):
        saved = self.client.put("/api/settings", json={
            "font_scale": 115,
            "message_density": "relaxed",
            "code_theme": "contrast",
            "stream_speed": "slow",
        }).json()
        self.assertEqual(saved["font_scale"], 115)
        self.assertEqual(saved["message_density"], "relaxed")
        self.assertEqual(saved["code_theme"], "contrast")
        self.assertEqual(saved["stream_speed"], "slow")
        loaded = self.client.get("/api/bootstrap").json()["settings"]
        self.assertEqual(loaded["font_scale"], 115)
        self.assertEqual(loaded["stream_speed"], "slow")

    def test_proactive_question_setting_is_persisted(self):
        saved = self.client.put("/api/settings", json={"proactive_questions": True}).json()
        self.assertTrue(saved["proactive_questions"])
        self.assertTrue(self.client.get("/api/bootstrap").json()["settings"]["proactive_questions"])

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

    def test_recent_memory_is_available_when_greeting_has_no_keywords(self):
        memory = self.client.post("/api/memories", json={"title": "重要关系", "content": "用户把阿澄视为长期陪伴者", "kind": "relationship"}).json()
        with app_module.closing(app_module.db()) as connection:
            results = app_module.retrieve_memories(connection, "你好")
        self.assertEqual(results[0]["id"], memory["id"])

    def test_vector_recall_finds_semantic_match_and_ignores_stale_content(self):
        semantic = self.client.post("/api/memories", json={
            "title": "rain walk", "content": "walked by the river and finally felt calm", "kind": "event",
        }).json()
        unrelated = self.client.post("/api/memories", json={
            "title": "breakfast", "content": "drank hot milk in the morning", "kind": "preference",
        }).json()
        with app_module.closing(app_module.db()) as connection:
            connection.executemany(
                "INSERT INTO memory_embeddings VALUES (?,?,?,?,?,?,?)",
                [
                    (semantic["id"], "route", "embed", app_module.memory_content_hash(semantic["title"], semantic["content"]), 2, "[1,0]", app_module.now_iso()),
                    (unrelated["id"], "route", "embed", app_module.memory_content_hash(unrelated["title"], unrelated["content"]), 2, "[0,1]", app_module.now_iso()),
                ],
            )
            connection.commit()
            results = app_module.retrieve_memories(
                connection, "a peaceful experience",
                query_vector=[1.0, 0.0], embedding_provider_id="route", embedding_model="embed",
            )
        self.assertEqual(results[0]["id"], semantic["id"])
        self.client.put(f"/api/memories/{semantic['id']}", json={
            "title": semantic["title"], "content": "changed content", "kind": semantic["kind"],
        })
        with app_module.closing(app_module.db()) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM memory_embeddings WHERE memory_id=?", (semantic["id"],)
            ).fetchone())

    def test_vector_rebuild_and_status_are_versioned_by_route_and_model(self):
        provider = self.client.post("/api/providers", json={
            "name": "Embedding", "protocol": "openai", "base_url": "https://example.com/v1",
            "api_key": "secret", "model": "chat",
        }).json()
        self.client.post("/api/memories", json={"title": "one", "content": "first memory", "kind": "fact"})
        self.client.post("/api/memories", json={"title": "two", "content": "second memory", "kind": "fact"})
        self.client.put("/api/settings", json={
            "vector_memory_enabled": True,
            "embedding_provider_id": provider["id"],
            "embedding_model": "text-embedding-test",
        })

        async def fake_embeddings(_provider, _model, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        with patch.object(app_module, "create_embeddings", side_effect=fake_embeddings):
            rebuilt = self.client.post("/api/memories/vector/rebuild", json={}).json()
        self.assertEqual(rebuilt["indexed"], 2)
        self.assertEqual(rebuilt["dimensions"], 3)
        status = self.client.get("/api/memories/vector/status").json()
        self.assertEqual((status["indexed"], status["stale"]), (2, 0))
        self.client.put("/api/settings", json={
            "vector_memory_enabled": True,
            "embedding_provider_id": provider["id"],
            "embedding_model": "another-model",
        })
        self.assertEqual(self.client.get("/api/memories/vector/status").json()["stale"], 2)

    def test_selected_persona_is_explicitly_injected_into_chat_context(self):
        provider = self.client.post("/api/providers", json={"name":"DS","protocol":"deepseek","base_url":"https://api.deepseek.com","model":"chat"}).json()
        persona = self.client.post("/api/personas", json={"name":"阿澄","prompt":"你叫阿澄，记得自己的名字。"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id":provider["id"],"persona_id":persona["id"]}).json()
        body = app_module.ChatIn(conversation_id=conversation["id"], content="你是谁", provider_id=provider["id"], persona_id=persona["id"])
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        self.assertIn('<assistant_persona active="true">', messages[0]["content"])
        self.assertIn("你叫阿澄", messages[0]["content"])

    def test_persona_can_be_edited_and_deleted_without_dangling_conversation(self):
        persona = self.client.post("/api/personas", json={"name":"朋友","prompt":"你叫 Ara。"}).json()
        conversation = self.client.post("/api/conversations", json={"persona_id":persona["id"]}).json()
        updated = self.client.put(f"/api/personas/{persona['id']}", json={"name":"挚友","prompt":"你叫 Ara，是长期朋友。"}).json()
        self.assertEqual(updated["id"], persona["id"])
        self.assertIn("长期朋友", updated["prompt"])
        self.assertEqual(self.client.delete(f"/api/personas/{persona['id']}").status_code, 200)
        bootstrap = self.client.get("/api/bootstrap").json()
        self.assertEqual(bootstrap["personas"], [])
        self.assertIsNone(next(item for item in bootstrap["conversations"] if item["id"] == conversation["id"])["persona_id"])

    def test_persona_workspace_config_is_persisted(self):
        config = {"startup_chat": "new", "memory_enabled": False, "history_enabled": False, "summary_frequency": 5, "quick_phrases": ["继续说"], "custom_headers": {"X-Mode": "friend"}, "custom_body": {"seed": 7}, "regex_rules": [{"pattern": "A", "replacement": "B"}], "tools": {"time": True, "calculator": False}, "mcp_servers": ["memory"]}
        persona = self.client.post("/api/personas", json={"name": "工作台", "prompt": "保持温柔", "config": config}).json()
        self.assertFalse(persona["config"]["memory_enabled"])
        self.assertEqual(persona["config"]["quick_phrases"], ["继续说"])
        self.assertEqual(persona["config"]["startup_chat"], "new")
        loaded = next(item for item in self.client.get("/api/bootstrap").json()["personas"] if item["id"] == persona["id"])
        self.assertEqual(loaded["config"]["custom_headers"]["X-Mode"], "friend")
        self.assertFalse(loaded["config"]["tools"]["calculator"])

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
        self.assertTrue(default_state["enabled"])
        self.assertEqual(default_state["state"]["drives"]["joy"], 35)

    def test_memories_and_vectors_are_scoped_per_persona(self):
        first = self.client.post("/api/memories", json={
            "title": "甲的秘密", "content": "只属于甲", "persona_key": "persona-a"
        }).json()
        self.client.post("/api/memories", json={
            "title": "乙的秘密", "content": "只属于乙", "persona_key": "persona-b"
        })
        self.assertEqual([item["id"] for item in self.client.get("/api/memories?persona_key=persona-a").json()], [first["id"]])
        with app_module.closing(app_module.db()) as connection:
            recalled = app_module.retrieve_memories(connection, "秘密", persona_key="persona-a")
        self.assertEqual([item["id"] for item in recalled], [first["id"]])

    def test_conversation_delete_does_not_touch_other_conversations(self):
        first = self.client.post("/api/conversations", json={"title": "甲", "persona_id": "persona-a"}).json()
        second = self.client.post("/api/conversations", json={"title": "乙", "persona_id": "persona-b"}).json()
        self.assertEqual(self.client.delete(f"/api/conversations/{first['id']}").status_code, 200)
        with app_module.closing(app_module.db()) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM conversations WHERE id=?", (first["id"],)).fetchone())
            self.assertIsNotNone(connection.execute("SELECT 1 FROM conversations WHERE id=?", (second["id"],)).fetchone())

    def test_homestead_is_persona_scoped_and_exposes_separate_api(self):
        first = self.client.post("/api/homestead/action?persona_id=flora-a", json={"action": "plant", "target": 0, "species": "sunbell"}).json()
        second = self.client.get("/api/homestead?persona_id=flora-b").json()
        self.assertEqual(first["state"]["garden"][0]["species"], "sunbell")
        self.assertIsNone(second["state"]["garden"][0])
        self.assertIn("flowers", first["catalog"])
        self.assertTrue(first["allowed_actions"])

    def test_homestead_ai_management_requires_explicit_authorization(self):
        untouched = self.client.post("/api/homestead/ai-manage?persona_id=ai-garden").json()
        self.assertIsNone(untouched["ai_action"])
        self.client.post("/api/homestead/action?persona_id=ai-garden", json={
            "action": "configure_management", "enabled": True, "max_actions_per_day": 4, "daily_budget": 30,
        })
        managed = self.client.post("/api/homestead/ai-manage?persona_id=ai-garden").json()
        self.assertIsNotNone(managed["ai_action"])

    def test_motivation_rejects_unknown_event_names(self):
        response = self.client.post("/api/motivation/__default__/event", json={"event": "unknown_legacy_event"})
        self.assertEqual(response.status_code, 422)

    def test_motivation_offline_mode_persists_and_reset_preserves_it(self):
        saved = self.client.put("/api/motivation/__default__/enabled", json={"enabled": True, "offline_mode": "frozen"}).json()
        self.assertEqual(saved["offline_mode"], "frozen")
        self.assertEqual(self.client.get("/api/motivation/__default__").json()["offline_mode"], "frozen")
        reset = self.client.post("/api/motivation/__default__/reset").json()
        self.assertTrue(reset["enabled"])
        self.assertEqual(reset["state"]["tick_count"], 0)
        self.assertEqual(self.client.get("/api/motivation/__default__").json()["offline_mode"], "frozen")

    def test_journal_and_board_visibility_is_enforced(self):
        public = self.client.post("/api/journals/persona-a", json={
            "title": "together", "content": "visible", "space": "shared",
            "author": "user", "visible_to_user": True, "visible_to_ai": True,
        }).json()
        self.client.post("/api/journals/persona-a", json={
            "title": "sealed", "content": "must not leak", "space": "ai",
            "author": "ai", "visible_to_user": False, "visible_to_ai": True,
        })
        listed = self.client.get("/api/journals/persona-a").json()
        self.assertEqual([item["id"] for item in listed["entries"]], [public["id"]])
        self.assertEqual(listed["sealed_count"], 1)
        board = self.client.post("/api/board/persona-a", json={"content": "hello", "visible_to_ai": False}).json()
        self.assertEqual(self.client.get("/api/board/persona-a").json()["messages"][0]["id"], board["id"])
        reply = self.client.post("/api/board/persona-a", json={"content": "reply", "visible_to_ai": True, "reply_to": board["id"]}).json()
        self.assertEqual(reply["reply_to"], board["id"])

    def test_ai_diary_tool_writes_as_ai_and_can_seal_content(self):
        result = asyncio.run(app_module.invoke_builtin_tool("journal_create", {
            "_persona_key": "persona-ai", "title": "private", "content": "inner",
            "space": "ai", "visible_to_user": False,
        }))
        self.assertTrue(result["sealed"])
        listed = self.client.get("/api/journals/persona-ai").json()
        self.assertEqual(listed["entries"], [])
        self.assertEqual(listed["sealed_count"], 1)

    def test_original_fishing_game_has_isolated_persistent_saves(self):
        catalog = self.client.get("/api/games").json()
        self.assertIn("quiet_fishing", [item["id"] for item in catalog])
        self.assertEqual(catalog[0]["id"], "homestead")
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
        checkin = self.client.post("/api/games/claw_machine/action", json={"action": "check_in"}).json()
        self.assertEqual(checkin["state"]["coins"], 140)
        self.assertEqual(self.client.post("/api/games/claw_machine/action", json={"action": "check_in"}).status_code, 409)
        with app_module.closing(app_module.db()) as connection:
            state = app_module.load_game(connection, "claw_machine", None)
            state["inventory"] = {"橘子猫": 2}
            app_module.save_game(connection, "claw_machine", None, state)
            connection.commit()
        sold = self.client.post("/api/games/claw_machine/action", json={"action": "sell_all"}).json()
        self.assertEqual(sold["state"]["coins"], 184)
        self.assertEqual(sold["state"]["inventory"], {})
        slots = self.client.post("/api/games/cloud_slots/action", json={"action": "spin", "amount": 1}).json()
        self.assertEqual(slots["state"]["turn"], 1)
        self.assertEqual(len(slots["state"]["reels"]), 3)

    def test_star_merge_is_a_deterministic_shared_puzzle(self):
        merged, score = app_module.move_star_merge(
            [2, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], "left"
        )
        self.assertEqual(merged[:4], [4, 4, 0, 0])
        self.assertEqual(score, 8)
        played = self.client.post("/api/games/star_merge/action", json={"action": "left"}).json()
        self.assertEqual(played["state"]["turn"], 1)
        self.assertEqual(played["state"]["score"], 0)
        self.assertEqual(sum(value > 0 for value in played["state"]["board"]), 3)
        loaded = self.client.get("/api/games/star_merge/state").json()["state"]
        self.assertEqual(loaded["board"], played["state"]["board"])
        reset = self.client.post("/api/games/star_merge/action", json={"action": "reset"}).json()["state"]
        self.assertEqual((reset["turn"], reset["score"], reset["best"]), (0, 0, 2))

    def test_star_merge_can_undo_the_last_verified_move(self):
        before = self.client.get("/api/games/star_merge/state").json()["state"]
        played = self.client.post("/api/games/star_merge/action", json={"action": "left"}).json()["state"]
        self.assertEqual(len(played["history"]), 1)
        undone = self.client.post("/api/games/star_merge/action", json={"action": "undo"}).json()["state"]
        self.assertEqual(undone["board"], before["board"])
        self.assertEqual(undone["turn"], before["turn"])
        self.assertEqual(undone["history"], [])

    def test_game_actions_feed_the_shared_room_context(self):
        played = self.client.post("/api/games/star_merge/action", json={"action": "left"}).json()["state"]
        self.assertEqual(played["room_messages"][-1]["role"], "event")
        self.assertIn("向左滑动", played["room_messages"][-1]["content"])
        self.assertIn("room_messages", app_module.default_fishing_state())
        self.assertIn("last_thought", app_module.default_claw_state())

    def test_maze_and_dungeon_use_verified_host_rules(self):
        catalog_ids = {item["id"] for item in self.client.get("/api/games").json()}
        self.assertTrue({"mist_maze", "ember_dungeon"}.issubset(catalog_ids))
        maze = self.client.post("/api/games/mist_maze/action", json={"action": "reset"}).json()["state"]
        self.assertEqual((len(maze["grid"]), maze["level"]), (9, 1))
        queue = [(tuple(maze["player"]), [])]
        seen = {tuple(maze["player"])}
        directions = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
        path = None
        while queue:
            (row, column), steps = queue.pop(0)
            if [row, column] == maze["goal"]:
                path = steps
                break
            for name, (dr, dc) in directions.items():
                target = (row + dr, column + dc)
                if maze["grid"][target[0]][target[1]] != "#" and target not in seen:
                    seen.add(target)
                    queue.append((target, steps + [name]))
        self.assertTrue(path)
        for direction in path:
            advanced = self.client.post("/api/games/mist_maze/action", json={"action": direction})
            self.assertEqual(advanced.status_code, 200, f"{direction} {advanced.text} path={path} grid={maze['grid']}")
        next_maze = advanced.json()["state"]
        self.assertEqual(next_maze["level"], 2)
        self.assertNotEqual(next_maze["grid"], maze["grid"])
        explored = self.client.post("/api/games/ember_dungeon/action", json={"action": "explore"}).json()["state"]
        self.assertIsNotNone(explored["enemy"])
        fought = self.client.post("/api/games/ember_dungeon/action", json={"action": "guard"}).json()["state"]
        self.assertLess(fought["enemy"]["hp"], fought["enemy"]["max_hp"])
        self.assertEqual(fought["room_messages"][-1]["role"], "event")

    def test_ai_game_turn_budget_allows_nine_and_autonomous_mode(self):
        parsed = app_module.AiGameTurnIn(provider_id="provider", turns=9, autonomous=True)
        self.assertEqual(parsed.turns, 9)
        self.assertTrue(parsed.autonomous)
        self.assertFalse(app_module.ai_game_wants_continue('{"action":"left","continue_playing":false}'))
        _, comment = app_module.parse_ai_game_choice('{"action":"left","comment":"', "star_merge")
        self.assertEqual(comment, "")

    def test_star_merge_ai_actions_and_fallback_only_choose_legal_moves(self):
        choice, _ = app_module.parse_ai_game_choice(
            '{"action":"down","comment":"keep the large tile low"}', "star_merge"
        )
        self.assertEqual(choice["action"], "down")
        state = app_module.default_star_merge_state()
        fallback, _ = app_module.fallback_ai_game_choice("star_merge", state, 0)
        moved, _ = app_module.move_star_merge(state["board"], fallback["action"])
        self.assertNotEqual(moved, state["board"])
        with self.assertRaises(Exception):
            app_module.parse_ai_game_choice('{"action":"reset"}', "star_merge")

    def test_ai_game_choices_are_whitelisted_and_budgeted(self):
        choice, comment = app_module.parse_ai_game_choice('{"action":"grab","amount":9,"comment":"试试中间"}', "claw_machine")
        self.assertEqual(choice, {"action": "grab", "amount": 1, "target": ""})
        self.assertEqual(comment, "试试中间")
        self.assertEqual(app_module.game_action_cost("claw_machine", choice, {}), 10)
        with self.assertRaises(Exception):
            app_module.parse_ai_game_choice('{"action":"delete_save"}', "claw_machine")

    def test_ai_game_plain_text_and_fallback_still_produce_safe_actions(self):
        choice, comment = app_module.parse_ai_game_choice("我想先抛竿看看水面。", "quiet_fishing")
        self.assertEqual(choice["action"], "cast")
        self.assertIn("抛竿", comment)
        fallback, fallback_comment = app_module.fallback_ai_game_choice("quiet_fishing", {"bait": 3, "coins": 0, "catch": {}}, 0)
        self.assertEqual(fallback, {"action": "cast", "amount": 1})
        self.assertEqual(fallback_comment, "")

    def test_device_local_time_is_injected_into_chat_context(self):
        provider = self.client.post("/api/providers", json={"name": "时间测试", "protocol": "openai", "base_url": "https://example.com/v1", "api_key": "test", "model": "test-model"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id": provider["id"]}).json()
        body = app_module.ChatIn(conversation_id=conversation["id"], content="现在几点", provider_id=provider["id"], local_time="2026年7月19日 星期日 17:30:00 GMT+8")
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("2026年7月19日", messages[0]["content"])
        self.assertIn("由用户设备提供", messages[0]["content"])

    def test_verified_game_result_is_injected_into_chat_context(self):
        provider = self.client.post("/api/providers", json={"name": "游戏测试", "protocol": "openai", "base_url": "https://example.com/v1", "api_key": "test", "model": "test-model"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id": provider["id"]}).json()
        body = app_module.ChatIn(conversation_id=conversation["id"], content="你去钓鱼", provider_id=provider["id"], game_context="Ara 钓到了银尾鲫，心里很开心。")
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        self.assertIn("verified_game_context", messages[0]["content"])
        self.assertIn("Ara 钓到了银尾鲫", messages[0]["content"])
        self.assertIn("游戏工具", messages[0]["content"])
        self.assertIn("只有收到已执行结果才能声称自己实际操作过", messages[0]["content"])

    def test_chat_message_template_formats_provider_copy_without_changing_source(self):
        source = [{"role": "user", "content": "你好"}, {"role": "system", "content": "规则"}]
        formatted = app_module.format_provider_chat_messages(source, "{{role}}｜{{date}}｜{{message}}")
        self.assertRegex(formatted[0]["content"], r"^用户｜\d{4}-\d{2}-\d{2}｜你好$")
        self.assertEqual(formatted[1]["content"], "规则")
        self.assertEqual(source[0]["content"], "你好")

    def test_typing_presence_contains_metadata_but_not_unsent_text(self):
        provider = self.client.post("/api/providers", json={"name": "输入状态", "protocol": "openai", "base_url": "https://example.com/v1", "api_key": "test", "model": "test-model"}).json()
        conversation = self.client.post("/api/conversations", json={"provider_id": provider["id"]}).json()
        body = app_module.ChatIn(conversation_id=conversation["id"], content="最终发出的消息", provider_id=provider["id"], typing_context="用户输入约 8 秒，发送前停顿约 2 秒。")
        with app_module.closing(app_module.db()) as connection:
            _, _, messages = app_module.load_chat_context(connection, body)
        self.assertIn("<typing_presence>", messages[0]["content"])
        self.assertIn("不含未发送正文", messages[0]["content"])

    def test_dream_vault_can_store_and_claim_a_quarantined_dream(self):
        created = self.client.post("/api/dreams/persona-a", json={"title": "雾里的门", "raw_text": "我梦见一扇门。", "kind": "quarantined"}).json()
        self.assertFalse(created["claimed"])
        claimed = self.client.post(f"/api/dreams/persona-a/{created['id']}/claim", json={"note": "愿意留下它"}).json()
        self.assertTrue(claimed["claimed"])
        self.assertEqual(claimed["claim_note"], "愿意留下它")
        self.assertEqual(self.client.get("/api/dreams/persona-a").json()["entries"][0]["title"], "雾里的门")


if __name__ == "__main__":
    unittest.main()
