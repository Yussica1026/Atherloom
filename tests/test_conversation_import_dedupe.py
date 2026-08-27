from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as app_module


FIXTURES = Path(__file__).parent / "fixtures" / "importers"


class ConversationImportDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = app_module.DB_PATH
        app_module.DB_PATH = Path(self.tempdir.name) / "imports.db"
        app_module.init_db()
        app_module.init_conversation_import_schema()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        app_module.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def preview(self, payload: object) -> dict:
        response = self.client.post(
            "/api/imports/preview",
            json={
                "payload": payload,
                "source_name": "external/claude-conversations.json",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_exact_duplicate_is_excluded_by_default_but_can_be_reimported(self) -> None:
        payload = json.loads(
            (FIXTURES / "claude-conversations.json").read_text(encoding="utf-8")
        )

        first_preview = self.preview(payload)
        first_summary = first_preview["conversation_summaries"][0]
        canonical_id = first_summary["canonical_conversation_id"]
        source_id = first_summary["source_conversation_id"]
        self.assertFalse(first_summary["possible_duplicate"])
        self.assertEqual(first_summary["existing_conversation_ids"], [])
        self.assertEqual(first_preview["statistics"]["possible_duplicate_count"], 0)
        self.assertEqual(
            first_preview["selection"]["selected_conversation_ids"],
            [canonical_id],
        )

        first_commit_response = self.client.post(
            f"/api/imports/batches/{first_preview['id']}/commit"
        )
        self.assertEqual(
            first_commit_response.status_code,
            200,
            first_commit_response.text,
        )
        first_created_id = first_commit_response.json()["created_conversation_ids"][0]

        second_preview = self.preview(payload)
        second_summary = second_preview["conversation_summaries"][0]
        self.assertTrue(second_summary["possible_duplicate"])
        self.assertEqual(
            second_summary["existing_conversation_ids"],
            [first_created_id],
        )
        self.assertFalse(second_summary["selected"])
        self.assertEqual(second_preview["statistics"]["possible_duplicate_count"], 1)
        self.assertEqual(
            second_preview["selection"]["selected_conversation_ids"],
            [],
        )
        self.assertEqual(
            second_preview["selection"]["excluded_conversation_ids"],
            [canonical_id],
        )

        fetched_response = self.client.get(
            f"/api/imports/batches/{second_preview['id']}"
        )
        self.assertEqual(fetched_response.status_code, 200, fetched_response.text)
        fetched = fetched_response.json()
        self.assertEqual(
            fetched["conversation_summaries"][0]["existing_conversation_ids"],
            [first_created_id],
        )
        self.assertTrue(fetched["conversation_summaries"][0]["possible_duplicate"])
        self.assertEqual(fetched["selection"]["selected_conversation_ids"], [])

        explicit_commit_response = self.client.post(
            f"/api/imports/batches/{second_preview['id']}/commit",
            json={"selected_conversation_ids": [canonical_id]},
        )
        self.assertEqual(
            explicit_commit_response.status_code,
            200,
            explicit_commit_response.text,
        )
        explicit_commit = explicit_commit_response.json()
        second_created_id = explicit_commit["created_conversation_ids"][0]
        self.assertNotEqual(second_created_id, first_created_id)

        with app_module.closing(app_module.db()) as connection:
            stored_conversation_ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM conversations WHERE id IN (?,?)",
                    (first_created_id, second_created_id),
                )
            }
            provenance_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM import_conversation_provenance AS imported
                JOIN import_batches AS batch ON batch.id=imported.batch_id
                WHERE batch.platform='claude'
                  AND batch.status='committed'
                  AND imported.source_conversation_id=?
                """,
                (source_id,),
            ).fetchone()[0]
        self.assertEqual(
            stored_conversation_ids,
            {first_created_id, second_created_id},
        )
        self.assertEqual(provenance_count, 2)


if __name__ == "__main__":
    unittest.main()
