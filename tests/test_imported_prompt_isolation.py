from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as app_module


class ImportedPromptIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = app_module.DB_PATH
        app_module.DB_PATH = Path(self.tempdir.name) / "prompt-isolation.db"
        app_module.init_db()
        app_module.init_conversation_import_schema()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        app_module.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def test_external_system_message_stays_readable_but_never_enters_model_context(self):
        provider = self.client.post(
            "/api/providers",
            json={
                "name": "测试线路",
                "protocol": "openai",
                "base_url": "https://example.com/v1",
                "model": "example-model",
            },
        ).json()
        persona = self.client.post(
            "/api/personas",
            json={"name": "现有人格", "prompt": "保持现有人格。"},
        ).json()
        external_prompt = "EXTERNAL_SYSTEM_MUST_REMAIN_HISTORY_ONLY"
        export = [{
            "id": "external-system-conversation",
            "title": "外部 system 历史",
            "current_node": "assistant-node",
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": ["system-node"],
                    "message": None,
                },
                "system-node": {
                    "id": "system-node",
                    "parent": "root",
                    "children": ["user-node"],
                    "message": {
                        "id": "system-message",
                        "author": {"role": "system"},
                        "content": {"content_type": "text", "parts": [external_prompt]},
                    },
                },
                "user-node": {
                    "id": "user-node",
                    "parent": "system-node",
                    "children": ["assistant-node"],
                    "message": {
                        "id": "user-message",
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": ["这是一条历史提问。"]},
                    },
                },
                "assistant-node": {
                    "id": "assistant-node",
                    "parent": "user-node",
                    "children": [],
                    "message": {
                        "id": "assistant-message",
                        "author": {"role": "assistant"},
                        "content": {"content_type": "text", "parts": ["这是一条历史回答。"]},
                    },
                },
            },
        }]
        preview_response = self.client.post(
            "/api/imports/preview",
            json={"payload": export, "source_name": "conversations.json"},
        )
        self.assertEqual(preview_response.status_code, 201, preview_response.text)
        preview = preview_response.json()
        assistant_identity = next(
            identity
            for identity in preview["source_identities"]
            if identity["kind"] == "assistant"
        )
        committed_response = self.client.post(
            f"/api/imports/batches/{preview['id']}/commit",
            json={
                "persona_mapping": {assistant_identity["id"]: persona["id"]},
            },
        )
        self.assertEqual(committed_response.status_code, 200, committed_response.text)
        conversation_id = committed_response.json()["created_conversation_ids"][0]

        stored = self.client.get(f"/api/conversations/{conversation_id}/messages").json()
        self.assertTrue(any(item["role"] == "system" and external_prompt in item["content"] for item in stored))

        body = app_module.ChatIn(
            conversation_id=conversation_id,
            content="继续聊天",
            provider_id=provider["id"],
            persona_id=persona["id"],
        )
        with app_module.closing(app_module.db()) as connection:
            _, _, model_messages = app_module.load_chat_context(connection, body)
            casual_messages = app_module.casual_recent_chat_messages(connection, conversation_id)
        self.assertFalse(any(external_prompt in item["content"] for item in model_messages))
        self.assertFalse(any(external_prompt in item["content"] for item in casual_messages))
        self.assertTrue(any("这是一条历史提问" in item["content"] for item in model_messages))
        self.assertTrue(any("这是一条历史回答" in item["content"] for item in model_messages))


if __name__ == "__main__":
    unittest.main()
