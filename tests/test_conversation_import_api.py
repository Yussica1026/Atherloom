from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as app_module


FIXTURES = Path(__file__).parent / "fixtures" / "importers"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ConversationImportApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = app_module.DB_PATH
        app_module.DB_PATH = Path(self.tempdir.name) / "imports.db"
        app_module.init_db()
        app_module.init_conversation_import_schema()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        app_module.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def preview(self, name: str = "chatgpt-conversations.json") -> dict:
        response = self.client.post(
            "/api/imports/preview",
            json={
                "payload": fixture(name),
                "source_name": f"external/{name}",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def commit(self, batch_id: str) -> dict:
        response = self.client.post(f"/api/imports/batches/{batch_id}/commit")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_preview_is_persistent_but_does_not_create_conversations_or_memories(self):
        formats_payload = self.client.get("/api/imports/formats").json()
        self.assertTrue(
            {"chatgpt.conversations", "claude.conversations"}.issubset(
                formats_payload["formats"]
            )
        )
        options = {item["id"]: item for item in formats_payload["format_options"]}
        self.assertEqual(
            options["chatgpt.conversations"]["file_extensions"], [".json", ".zip"]
        )
        self.assertEqual(
            options["astrbot.conversations_jsonl"]["file_extensions"], [".jsonl"]
        )
        self.assertEqual(
            formats_payload["upload"]["endpoint"], "/api/imports/preview-file"
        )

        preview = self.preview()
        self.assertEqual(preview["status"], "previewed")
        self.assertEqual(preview["format_id"], "chatgpt.conversations")
        self.assertEqual(preview["conversation_count"], 1)
        self.assertEqual(preview["message_count"], 3)
        self.assertEqual(preview["preview"][0]["branch_count"], 2)
        self.assertEqual(preview["statistics"]["role_counts"], {"assistant": 2, "user": 1})
        self.assertEqual(preview["statistics"]["attachment_count"], 1)
        self.assertEqual(preview["statistics"]["source_identity_count"], 2)
        self.assertEqual(
            preview["selection"]["selected_conversation_ids"],
            [preview["preview"][0]["canonical_conversation_id"]],
        )
        summary = preview["conversation_summaries"][0]
        self.assertTrue(summary["selected"])
        self.assertEqual(summary["warning_count"], 0)
        self.assertEqual(
            {identity["kind"] for identity in summary["source_identities"]},
            {"user", "assistant"},
        )
        self.assertEqual(
            preview["persona_mapping_defaults"],
            {identity["id"]: None for identity in preview["source_identities"]},
        )
        self.assertEqual(
            preview["defaults"],
            {
                "archived": True,
                "provider_id": None,
                "persona_id": None,
                "memories_created": 0,
            },
        )
        with app_module.closing(app_module.db()) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0
            )
            stored = connection.execute(
                "SELECT status FROM import_batches WHERE id=?", (preview["id"],)
            ).fetchone()
        self.assertEqual(stored["status"], "previewed")

    def test_raw_json_and_jsonl_file_upload_create_preview_batches(self):
        response = self.client.post(
            "/api/imports/preview-file",
            params={"source_name": "chatgpt-conversations.json"},
            content=(FIXTURES / "chatgpt-conversations.json").read_bytes(),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        preview = response.json()
        self.assertEqual(preview["source_name"], "chatgpt-conversations.json")
        self.assertEqual(preview["format_id"], "chatgpt.conversations")
        self.assertEqual(preview["statistics"]["conversation_count"], 1)
        self.assertEqual(preview["statistics"]["message_count"], 3)
        self.assertEqual(preview["selection"]["default"], "all")

        jsonl_response = self.client.post(
            "/api/imports/preview-file",
            params={"source_name": "astrbot-conversations.jsonl"},
            content=(FIXTURES / "astrbot-conversations.jsonl").read_bytes(),
            headers={"content-type": "application/x-ndjson"},
        )
        self.assertEqual(jsonl_response.status_code, 201, jsonl_response.text)
        jsonl_preview = jsonl_response.json()
        self.assertEqual(
            jsonl_preview["format_id"], "astrbot.conversations_jsonl"
        )
        self.assertEqual(jsonl_preview["statistics"]["conversation_count"], 2)
        self.assertEqual(
            jsonl_preview["conversation_summaries"][0]["source_context"]["scope"],
            "group",
        )
        self.assertIn(
            "persona",
            {identity["kind"] for identity in jsonl_preview["source_identities"]},
        )
        with app_module.closing(app_module.db()) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                0,
            )

    def test_encoded_file_upload_preserves_kelivo_zip_bytes(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "chats.json",
                (FIXTURES / "kelivo-chats.json").read_bytes(),
            )
        encoded = base64.b64encode(archive_buffer.getvalue()).decode("ascii")

        response = self.client.post(
            "/api/imports/preview-encoded",
            json={
                "source_name": "kelivo-backup.zip",
                "content_base64": encoded,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        preview = response.json()
        self.assertEqual(preview["format_id"], "kelivo.chats_backup")
        self.assertEqual(preview["source_name"], "kelivo-backup.zip")
        self.assertGreater(preview["statistics"]["message_count"], 0)
        self.assertEqual(
            preview["conversation_count"],
            preview["statistics"]["conversation_count"],
        )

        invalid = self.client.post(
            "/api/imports/preview-encoded",
            json={
                "source_name": "kelivo-backup.zip",
                "content_base64": encoded + "\n",
            },
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)

    def test_commit_honors_selection_and_existing_persona_mapping(self):
        export = fixture("chatgpt-conversations.json")
        second = json.loads(json.dumps(export[0]))
        second["id"] = "chatgpt-conversation-anon-2"
        second["title"] = "只导入这一条"
        response = self.client.post(
            "/api/imports/preview",
            json={
                "payload": [export[0], second],
                "source_name": "two-conversations.json",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        preview = response.json()
        summaries = preview["conversation_summaries"]
        selected_id = summaries[1]["canonical_conversation_id"]
        assistant_identity = next(
            identity
            for identity in summaries[1]["source_identities"]
            if identity["kind"] == "assistant"
        )
        persona = self.client.post(
            "/api/personas",
            json={"name": "导入人格", "prompt": "保持来源身份的对话语气。"},
        ).json()

        committed = self.client.post(
            f"/api/imports/batches/{preview['id']}/commit",
            json={
                "selected_conversation_ids": [selected_id],
                "persona_mapping": {
                    assistant_identity["id"]: persona["id"],
                },
            },
        )
        self.assertEqual(committed.status_code, 200, committed.text)
        payload = committed.json()
        self.assertEqual(
            payload["commit_selection"]["selected_conversation_ids"],
            [selected_id],
        )
        self.assertEqual(
            payload["commit_selection"]["created_conversation_ids"],
            payload["created_conversation_ids"],
        )
        self.assertEqual(len(payload["created_conversation_ids"]), 1)
        self.assertEqual(payload["conversations"][0]["persona_id"], persona["id"])
        with app_module.closing(app_module.db()) as connection:
            created = connection.execute(
                "SELECT title,persona_id,archived FROM conversations WHERE id=?",
                (payload["created_conversation_ids"][0],),
            ).fetchone()
        self.assertEqual(created["title"], "只导入这一条")
        self.assertEqual(created["persona_id"], persona["id"])
        self.assertEqual(created["archived"], 1)

        invalid_preview = self.preview()
        assistant_identity_id = next(
            identity["id"]
            for identity in invalid_preview["source_identities"]
            if identity["kind"] == "assistant"
        )
        missing = self.client.post(
            f"/api/imports/batches/{invalid_preview['id']}/commit",
            json={"persona_mapping": {assistant_identity_id: "missing-persona"}},
        )
        self.assertEqual(missing.status_code, 409, missing.text)
        self.assertIn("missing-persona", missing.json()["detail"])

    def test_commit_archives_unbound_conversation_and_preserves_provenance(self):
        preview = self.preview()
        committed = self.commit(preview["id"])
        self.assertEqual(committed["status"], "committed")
        self.assertEqual(len(committed["conversations"]), 1)
        imported_id = committed["conversations"][0]["conversation_id"]

        with app_module.closing(app_module.db()) as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id=?", (imported_id,)
            ).fetchone()
            memory_count = connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0]
        self.assertEqual(conversation["title"], "匿名分支示例")
        self.assertIsNone(conversation["provider_id"])
        self.assertIsNone(conversation["persona_id"])
        self.assertEqual(conversation["archived"], 1)
        self.assertEqual(memory_count, 0)

        messages = self.client.get(
            f"/api/conversations/{imported_id}/messages"
        ).json()
        self.assertEqual(len(messages), 3)
        self.assertEqual([item["role"] for item in messages].count("assistant"), 2)
        current = next(item for item in messages if "当前分支回复" in item["content"])
        alternate = next(item for item in messages if "第一条分支回复" in item["content"])
        self.assertEqual(current["selected"], 1)
        self.assertEqual(alternate["selected"], 0)

        provenance = self.client.get(
            f"/api/imports/conversations/{imported_id}/provenance"
        )
        self.assertEqual(provenance.status_code, 200, provenance.text)
        provenance_payload = provenance.json()
        self.assertEqual(provenance_payload["batch"]["id"], preview["id"])
        self.assertEqual(len(provenance_payload["messages"]), 3)
        self.assertEqual(
            provenance_payload["conversation"]["provenance"]["provenance"][
                "extra"
            ]["future_conversation_field"],
            {"kept": True},
        )

        repeated = self.commit(preview["id"])
        self.assertEqual(
            [item["conversation_id"] for item in repeated["conversations"]],
            [imported_id],
        )
        with app_module.closing(app_module.db()) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3
            )

    def test_rollback_deletes_only_batch_objects_and_keeps_unrelated_conversation(self):
        unrelated = self.client.post(
            "/api/conversations", json={"title": "本地原生会话"}
        ).json()
        preview = self.preview("claude-conversations.json")
        committed = self.commit(preview["id"])
        imported_id = committed["conversations"][0]["conversation_id"]

        rollback = self.client.post(
            f"/api/imports/batches/{preview['id']}/rollback"
        )
        self.assertEqual(rollback.status_code, 200, rollback.text)
        self.assertEqual(rollback.json()["status"], "rolled_back")
        repeated = self.client.post(
            f"/api/imports/batches/{preview['id']}/rollback"
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        with app_module.closing(app_module.db()) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM conversations WHERE id=?", (imported_id,)
                ).fetchone()
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM conversations WHERE id=?", (unrelated["id"],)
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM memories WHERE source_conversation_id=?",
                    (imported_id,),
                ).fetchone()[0],
                0,
            )

    def test_rollback_is_atomic_when_native_message_was_appended(self):
        preview = self.preview("claude-conversations.json")
        committed = self.commit(preview["id"])
        imported_id = committed["conversations"][0]["conversation_id"]
        native_id = str(uuid.uuid4())
        with app_module.closing(app_module.db()) as connection:
            connection.execute(
                """
                INSERT INTO messages(
                  id,conversation_id,role,content,provider_id,model,
                  created_at,reasoning,parent_message_id
                ) VALUES(?,?,'user','导入后追加的原生消息',NULL,NULL,?,'',NULL)
                """,
                (native_id, imported_id, app_module.now_iso()),
            )
            connection.commit()

        blocked = self.client.post(
            f"/api/imports/batches/{preview['id']}/rollback"
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("追加原生消息", blocked.json()["detail"])
        with app_module.closing(app_module.db()) as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM conversations WHERE id=?", (imported_id,)
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                    (imported_id,),
                ).fetchone()[0],
                committed["message_count"] + 1,
            )
            status = connection.execute(
                "SELECT status FROM import_batches WHERE id=?", (preview["id"],)
            ).fetchone()["status"]
        self.assertEqual(status, "committed")

    def test_invalid_export_does_not_create_batch(self):
        invalid = self.client.post(
            "/api/imports/preview", json={"payload": {"unrelated": []}}
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)
        with app_module.closing(app_module.db()) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0],
                0,
            )

    def test_backup_round_trip_keeps_batch_and_provenance(self):
        preview = self.preview()
        committed = self.commit(preview["id"])
        conversation_id = committed["conversations"][0]["conversation_id"]

        exported = self.client.post(
            "/api/backup/export", json={"parts": ["conversations"]}
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        bundle = exported.json()
        for table in app_module.IMPORT_BACKUP_TABLES:
            self.assertIn(table, bundle["tables"])

        rolled_back = self.client.post(
            f"/api/imports/batches/{preview['id']}/rollback"
        )
        self.assertEqual(rolled_back.status_code, 200, rolled_back.text)
        restored = self.client.post(
            "/api/backup/restore",
            json={"bundle": bundle, "parts": ["conversations"]},
        )
        self.assertEqual(restored.status_code, 200, restored.text)

        batch = self.client.get(f"/api/imports/batches/{preview['id']}")
        self.assertEqual(batch.status_code, 200, batch.text)
        self.assertEqual(batch.json()["status"], "committed")
        provenance = self.client.get(
            f"/api/imports/conversations/{conversation_id}/provenance"
        )
        self.assertEqual(provenance.status_code, 200, provenance.text)
        self.assertEqual(len(provenance.json()["messages"]), 3)

        legacy_bundle = json.loads(json.dumps(bundle))
        for table in app_module.IMPORT_BACKUP_TABLES:
            legacy_bundle["tables"].pop(table)
        legacy_restore = self.client.post(
            "/api/backup/restore",
            json={"bundle": legacy_bundle, "parts": ["conversations"]},
        )
        self.assertEqual(legacy_restore.status_code, 200, legacy_restore.text)
        self.assertEqual(
            self.client.get(f"/api/imports/batches/{preview['id']}").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
