import json
import sqlite3
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from backend.importers import (
    DEFAULT_IMPORTER_REGISTRY,
    ImporterError,
    KelivoSQLiteBackupUnsupported,
    UnsupportedExportFormat,
    detect_export,
    parse_export,
)


FIXTURES = Path(__file__).parent / "fixtures" / "importers"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def zip_fixture(entries: dict[str, str | bytes]) -> bytes:
    """Build a small anonymous archive without committing binary test data."""

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value.encode("utf-8") if isinstance(value, str) else value)
    return output.getvalue()


def kelivo_sqlite_backup_fixture(
    *,
    schema_version: int = 1,
    malformed_rows: bool = False,
) -> bytes:
    """Generate the published Kelivo Drift v1 core schema entirely in memory."""

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(f"""
            PRAGMA user_version = {schema_version};
            CREATE TABLE conversation_rows (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, is_pinned INTEGER NOT NULL DEFAULT 0,
                assistant_id TEXT, truncate_index INTEGER NOT NULL DEFAULT -1,
                version_selections_json TEXT NOT NULL DEFAULT '{{}}', summary TEXT,
                last_summarized_message_count INTEGER NOT NULL DEFAULT 0,
                chat_suggestions_json TEXT NOT NULL DEFAULT '[]', injected_memory_hash TEXT,
                last_memory_extracted_order INTEGER NOT NULL DEFAULT -1
            );
            CREATE TABLE message_rows (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                timestamp INTEGER NOT NULL, model_id TEXT, provider_id TEXT, total_tokens INTEGER,
                is_streaming INTEGER NOT NULL DEFAULT 0, reasoning_start_at INTEGER,
                reasoning_finished_at INTEGER, translation TEXT, reasoning_segments_json TEXT,
                group_id TEXT, version INTEGER NOT NULL DEFAULT 0, prompt_tokens INTEGER,
                completion_tokens INTEGER, cached_tokens INTEGER, duration_ms INTEGER,
                message_order INTEGER NOT NULL
            );
            CREATE TABLE conversation_mcp_server_rows (
                conversation_id TEXT NOT NULL, server_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                PRIMARY KEY (conversation_id, server_id)
            );
            CREATE TABLE message_part_rows (
                part_id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                revision_id TEXT NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE provider_artifact_rows (
                conversation_id TEXT NOT NULL, revision_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY (revision_id, kind)
            );
        """)
        connection.execute(
            "INSERT INTO conversation_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sqlite-conversation-anon", "Anonymous SQLite conversation",
                1_706_745_600_000_000, 1_706_745_720_000_000, 0,
                "sqlite-assistant-anon", -1, '{"sqlite-assistant-group":0}',
                "Anonymous summary", 2, '["Continue?"]', None, 1,
            ),
        )
        connection.execute(
            "INSERT INTO conversation_mcp_server_rows VALUES (?,?,?)",
            ("sqlite-conversation-anon", "anonymous-mcp", 0),
        )
        message_sql = """
            INSERT INTO message_rows (
                id, conversation_id, role, timestamp, model_id, provider_id, total_tokens,
                is_streaming, reasoning_start_at, reasoning_finished_at, translation,
                reasoning_segments_json, group_id, version, prompt_tokens, completion_tokens,
                cached_tokens, duration_ms, message_order
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        connection.execute(message_sql, (
            "sqlite-user-anon", "sqlite-conversation-anon", "user",
            1_706_745_601_000_000, None, None, None, 0, None, None, None, None,
            "sqlite-user-group", 0, None, None, None, None, 0,
        ))
        connection.execute(message_sql, (
            "sqlite-assistant-anon-message", "sqlite-conversation-anon", "assistant",
            1_706_745_660_000_000, "anonymous-model", "anonymous-provider", 12, 0,
            1_706_745_650_000_000, 1_706_745_659_000_000, None, None,
            "sqlite-assistant-group", 0, 4, 8, 0, 9000, 1,
        ))
        parts = [
            ("sqlite-conversation-anon", "sqlite-user-anon", 0, "text", "Hello from SQLite"),
            ("sqlite-conversation-anon", "sqlite-assistant-anon-message", 0, "reasoning", "Anonymous reasoning"),
            ("sqlite-conversation-anon", "sqlite-assistant-anon-message", 1, "text", "SQLite reply"),
            (
                "sqlite-conversation-anon", "sqlite-assistant-anon-message", 2, "tool_call",
                '{"id":"anonymous-tool","name":"lookup","status":"success"}',
            ),
            (
                "sqlite-conversation-anon", "sqlite-assistant-anon-message", 3, "file",
                '{"uri":"kelivo-file://upload/anonymous.txt","name":"anonymous.txt","mime":"text/plain"}',
            ),
        ]
        for conversation_id, revision_id, ordinal, kind, payload in parts:
            connection.execute(
                """INSERT INTO message_part_rows
                   (conversation_id,revision_id,ordinal,kind,payload,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    conversation_id, revision_id, ordinal, kind, payload,
                    1_706_745_601_000_000, 1_706_745_720_000_000,
                ),
            )
        if malformed_rows:
            connection.execute(
                "UPDATE conversation_rows SET version_selections_json = ? WHERE id = ?",
                ("{anonymous-invalid-json", "sqlite-conversation-anon"),
            )
            for ordinal, kind, payload in (
                (4, "image", "{anonymous-invalid-json"),
                (5, "tool_call", "anonymous-not-a-json-object"),
            ):
                connection.execute(
                    """INSERT INTO message_part_rows
                       (conversation_id,revision_id,ordinal,kind,payload,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        "sqlite-conversation-anon",
                        "sqlite-assistant-anon-message",
                        ordinal,
                        kind,
                        payload,
                        1_706_745_660_000_000,
                        1_706_745_720_000_000,
                    ),
                )
        connection.execute(
            "INSERT INTO provider_artifact_rows VALUES (?,?,?,?,?,?)",
            (
                "sqlite-conversation-anon", "sqlite-assistant-anon-message",
                "gemini_thought_signature", "anonymous-sqlite-signature",
                1_706_745_660_000_000, 1_706_745_720_000_000,
            ),
        )
        connection.execute(
            "INSERT INTO provider_artifact_rows VALUES (?,?,?,?,?,?)",
            (
                "sqlite-conversation-anon", "sqlite-assistant-anon-message",
                "future_provider_artifact", '{"kept":true}',
                1_706_745_660_000_000, 1_706_745_720_000_000,
            ),
        )
        connection.commit()
        database = connection.serialize()
    finally:
        connection.close()

    manifest = {
        "format": "kelivo-backup",
        "formatVersion": 2,
        "payloadKind": "sqlite",
        "includeChats": True,
        "includeFiles": True,
        "database": {
            "entry": "database/kelivo.db",
            "schemaVersion": schema_version,
            "conversationCount": 1,
            "messageCount": 2,
        },
    }
    return zip_fixture({
        "manifest.json": json.dumps(manifest),
        "settings.json": "{}",
        "database/kelivo.db": database,
        "upload/anonymous.txt": "anonymous attachment",
    })


class ImporterDetectionTests(unittest.TestCase):
    def test_detects_each_official_export_shape(self):
        chatgpt = detect_export(fixture("chatgpt-conversations.json"), "conversations.json")
        claude = detect_export(fixture("claude-conversations.json"), "conversations.json")

        self.assertEqual(chatgpt.format_id, "chatgpt.conversations")
        self.assertEqual(chatgpt.platform, "chatgpt")
        self.assertEqual(claude.format_id, "claude.conversations")
        self.assertEqual(claude.platform, "claude")

    def test_accepts_utf8_bom_json_and_explicit_hint_for_empty_exports(self):
        raw = (FIXTURES / "chatgpt-conversations.json").read_bytes()
        parsed = parse_export(b"\xef\xbb\xbf" + raw, source_name="conversations.json")
        empty = parse_export([], format_hint="anthropic")

        self.assertEqual(parsed.platform, "chatgpt")
        self.assertEqual(empty.platform, "claude")
        self.assertEqual(empty.conversations, [])

    def test_rejects_invalid_json_and_unknown_shapes(self):
        with self.assertRaises(ImporterError):
            parse_export("{not-json")
        with self.assertRaises(UnsupportedExportFormat):
            parse_export({"unrelated": []})

    def test_registry_exposes_additive_format_metadata(self):
        details = {item.format_id: item for item in DEFAULT_IMPORTER_REGISTRY.list_format_details()}

        self.assertEqual(details["chatgpt.conversations"].file_extensions, (".json", ".zip"))
        self.assertIn("application/zip", details["claude.conversations"].media_types)
        self.assertEqual(details["astrbot.conversations_jsonl"].file_extensions, (".jsonl",))
        self.assertEqual(details["kelivo.chats_backup"].aliases[-1], "kelivo-session")
        self.assertEqual(details["kelivo.chats_backup"].file_extensions, (".json", ".zip"))
        self.assertIn("application/zip", details["kelivo.chats_backup"].media_types)


class ChatGPTConversationImporterTests(unittest.TestCase):
    def setUp(self):
        self.batch = parse_export(
            fixture("chatgpt-conversations.json"),
            source_name="chatgpt/conversations.json",
        )
        self.conversation = self.batch.conversations[0]

    def test_preserves_mapping_parents_and_all_branches(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        branches = {branch.leaf_node_id: branch for branch in self.conversation.branches}

        self.assertEqual(self.conversation.root_node_ids, ["root"])
        self.assertEqual(nodes["user-1"].parent_id, "root")
        self.assertEqual(nodes["user-1"].children_ids, ["assistant-branch-a", "assistant-branch-b"])
        self.assertEqual(set(branches), {"assistant-branch-a", "assistant-branch-b"})
        self.assertEqual(branches["assistant-branch-a"].node_ids, ["root", "user-1", "assistant-branch-a"])
        self.assertTrue(branches["assistant-branch-b"].is_current)
        self.assertIsNone(nodes["root"].message)

    def test_normalizes_known_content_and_keeps_unknown_blocks_verbatim(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        user_blocks = nodes["user-1"].message.content
        current_blocks = nodes["assistant-branch-b"].message.content

        self.assertEqual(user_blocks[0].kind, "multimodal_text")
        self.assertEqual(user_blocks[0].text, "请描述这张匿名图片。")
        self.assertEqual(user_blocks[1].kind, "image_asset_pointer")
        self.assertEqual(user_blocks[1].raw["future_block_field"], [1, 2, 3])
        self.assertEqual(current_blocks[1].kind, "future_audio_summary")
        self.assertEqual(current_blocks[1].raw["payload"], {"anonymous": True})

    def test_keeps_source_metadata_and_unknown_fields_in_provenance(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        user = nodes["user-1"]
        current = nodes["assistant-branch-b"].message

        self.assertEqual(self.batch.provenance.source_name, "chatgpt/conversations.json")
        self.assertEqual(self.conversation.provenance.extra["future_conversation_field"], {"kept": True})
        self.assertEqual(user.provenance.extra["future_node_field"], "kept")
        self.assertEqual(current.provenance.extra["future_message_field"], {"kept": True})
        self.assertEqual(current.provenance.source_role, "assistant")
        self.assertEqual(current.created_at, "2024-01-01T00:00:30.000000Z")

    def test_official_zip_entry_uses_chatgpt_detector_and_indexes_other_files(self):
        archive = zip_fixture({
            "conversations.json": json.dumps(
                fixture("chatgpt-conversations.json"), ensure_ascii=False
            ),
            "attachments/anonymous.png": b"anonymous-image-placeholder",
            "user.json": '{"id":"anonymous"}',
        })

        detected = detect_export(archive, "chatgpt-data-export.zip")
        batch = parse_export(archive, source_name="chatgpt-data-export.zip")
        container = batch.provenance.extra["container"]

        self.assertEqual(detected.format_id, "chatgpt.conversations")
        self.assertEqual(container["json_entry"], "conversations.json")
        self.assertIn("attachments/anonymous.png", container["attachment_entries"])
        self.assertIn("user.json", container["other_entries"])

    def test_large_official_zip_combines_numbered_conversation_files(self):
        archive = zip_fixture({
            "conversations-000.json": json.dumps(
                fixture("chatgpt-conversations.json"), ensure_ascii=False
            ),
            "conversations-001.json": "[]",
            "attachments/anonymous.bin": b"anonymous",
        })

        batch = parse_export(archive, source_name="large-chatgpt-export.zip")

        self.assertEqual(len(batch.conversations), 1)
        self.assertEqual(
            batch.provenance.extra["container"]["json_entries"],
            ["conversations-000.json", "conversations-001.json"],
        )

    def test_real_multimodal_parts_metadata_attachments_and_timestamps(self):
        batch = parse_export(fixture("chatgpt-real-variants.json"))
        conversation = batch.conversations[0]
        messages = {node.id: node.message for node in conversation.nodes}
        user = messages["user-node-anonymous"]
        assistant = messages["assistant-node-anonymous"]

        self.assertEqual(conversation.current_node_id, "assistant-node-anonymous")
        self.assertEqual(conversation.created_at, "2024-03-01T08:00:00.000000Z")
        self.assertEqual(user.created_at, "2024-03-01T08:00:01.250000Z")
        self.assertEqual(
            [block.kind for block in user.content],
            ["multimodal_text", "image_asset_pointer", "attachment"],
        )
        self.assertEqual(
            user.content[1].raw["asset_pointer"],
            "sediment://file_anonymous_image",
        )
        self.assertEqual(user.content[2].mime_type, "text/plain")
        self.assertTrue(user.content[2].raw["future_attachment_field"]["kept"])
        self.assertEqual(
            [block.kind for block in assistant.content],
            ["text", "unknown", "execution_output"],
        )
        self.assertEqual(assistant.content[2].text, "anonymous tool output")

    def test_current_outer_zip_with_conversation_and_file_part_zips(self):
        conversation_part = zip_fixture({
            "conversations-000.json": json.dumps(
                fixture("chatgpt-real-variants.json"), ensure_ascii=False
            ),
        })
        files_part = zip_fixture({
            "file-anonymous-upload.dat": b"anonymous file bytes",
            "personal/files/anonymous.txt": "anonymous text",
        })
        outer = zip_fixture({
            "Conversations__anonymous_part-001.zip": conversation_part,
            "Files__anonymous_files-001.zip": files_part,
            "User Profile/user.json": '{"id":"anonymous"}',
        })

        detected = detect_export(outer, "openai-data-export.zip")
        batch = parse_export(outer, source_name="openai-data-export.zip")
        container = batch.provenance.extra["container"]

        self.assertEqual(detected.format_id, "chatgpt.conversations")
        self.assertEqual(
            container["json_entry"],
            "Conversations__anonymous_part-001.zip!conversations-000.json",
        )
        self.assertIn(
            "Files__anonymous_files-001.zip!file-anonymous-upload.dat",
            container["attachment_entries"],
        )
        self.assertEqual(
            container["nested_entries"]["Files__anonymous_files-001.zip"],
            ["file-anonymous-upload.dat", "personal/files/anonymous.txt"],
        )


class ClaudeConversationImporterTests(unittest.TestCase):
    def setUp(self):
        self.batch = parse_export(
            fixture("claude-conversations.json"),
            source_name="claude/conversations.json",
        )
        self.conversation = self.batch.conversations[0]

    def test_preserves_explicit_parent_and_source_branch_provenance(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        branches = {branch.leaf_node_id: branch for branch in self.conversation.branches}

        self.assertEqual(nodes["claude-message-user"].message.role, "user")
        self.assertEqual(nodes["claude-message-main"].parent_id, "claude-message-user")
        self.assertEqual(nodes["claude-message-alt"].parent_id, "claude-message-user")
        self.assertEqual(nodes["claude-message-alt"].provenance.branch_id, "alternate")
        self.assertEqual(set(branches), {"claude-message-main", "claude-message-alt"})
        self.assertTrue(branches["claude-message-alt"].is_current)
        self.assertEqual(branches["claude-message-alt"].source_branch_id, "alternate")

    def test_keeps_thinking_future_blocks_and_attachments_without_assuming_schema(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        user_blocks = nodes["claude-message-user"].message.content
        main_blocks = nodes["claude-message-main"].message.content
        alt_blocks = nodes["claude-message-alt"].message.content

        self.assertEqual([block.kind for block in user_blocks], ["text", "attachment"])
        self.assertEqual(user_blocks[1].raw["file_name"], "anonymous.txt")
        self.assertEqual(user_blocks[1].mime_type, "text/plain")
        self.assertEqual(main_blocks[0].kind, "thinking")
        self.assertEqual(main_blocks[0].text, main_blocks[0].raw["thinking"])
        self.assertEqual(main_blocks[0].raw["signature"], "anonymous")
        self.assertEqual(alt_blocks[1].kind, "future_content_block")
        self.assertEqual(alt_blocks[1].raw["arbitrary"]["nested"], [True, None, 3])

    def test_real_official_blocks_attachments_files_identity_and_timestamps(self):
        batch = parse_export(fixture("claude-official-variants.json"))
        conversation = batch.conversations[0]
        nodes = {node.id: node for node in conversation.nodes}
        user = nodes["claude-official-user-anonymous"].message
        assistant = nodes["claude-official-assistant-anonymous"].message

        self.assertEqual(conversation.created_at, "2026-02-25T19:14:43.123456Z")
        self.assertEqual(conversation.updated_at, "2026-02-25T20:30:51.000000Z")
        self.assertEqual(conversation.current_node_id, "claude-official-assistant-anonymous")
        self.assertEqual(conversation.participants[0].id, "claude-account-anonymous")
        self.assertEqual(conversation.participants[0].kind, "account")
        self.assertEqual(
            [block.kind for block in user.content],
            ["text", "attachment", "file"],
        )
        self.assertEqual(user.content[1].text, "# Anonymous extracted attachment")
        self.assertEqual(user.content[1].mime_type, "text/markdown")
        self.assertEqual(user.content[1].raw["id"], "attachment-anonymous")
        self.assertEqual(user.content[2].raw["preview_asset"]["url"], "https://example.invalid/anonymous-asset")
        self.assertEqual(
            [block.kind for block in assistant.content],
            ["thinking", "tool_use", "tool_result", "text", "token_budget"],
        )
        self.assertEqual(assistant.content[0].text, "Anonymous exported thinking")
        self.assertEqual(assistant.content[2].text, "Anonymous tool result")
        self.assertTrue(assistant.content[4].raw["future_budget_field"]["kept"])

    def test_live_tree_files_v2_root_sentinel_and_text_fallback(self):
        batch = parse_export(fixture("claude-live-tree-variants.json"))
        conversation = batch.conversations[0]
        nodes = {node.id: node for node in conversation.nodes}
        branches = {branch.leaf_node_id: branch for branch in conversation.branches}
        user_node = nodes["claude-live-user-anonymous"]
        selected = nodes["claude-live-assistant-selected"].message

        self.assertEqual(conversation.created_at, "2026-07-27T10:00:00.000000Z")
        self.assertIsNone(user_node.parent_id)
        self.assertEqual(
            user_node.provenance.extra["raw_parent"],
            "00000000-0000-4000-8000-000000000000",
        )
        self.assertEqual(
            [block.kind for block in user_node.message.content],
            ["tool_use", "text", "file"],
        )
        self.assertEqual(user_node.message.content[1].text, "Use the presented anonymous file.")
        self.assertEqual(user_node.message.content[2].provenance.extra["source_field"], "files_v2")
        self.assertEqual(user_node.message.content[2].raw["future_file_field"], ["kept"])
        self.assertEqual(set(branches), {
            "claude-live-assistant-other",
            "claude-live-assistant-selected",
        })
        self.assertTrue(branches["claude-live-assistant-selected"].is_current)
        self.assertEqual(
            [block.kind for block in selected.content],
            ["tool_result", "text"],
        )
        self.assertEqual(selected.content[0].text, "Anonymous live result")
        self.assertEqual(selected.content[1].text, "Selected final answer.")

    def test_preserves_export_conversation_and_message_provenance(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        main_message = nodes["claude-message-main"].message
        alt_message = nodes["claude-message-alt"].message

        self.assertEqual(self.batch.provenance.extra["export_version"], "anonymous-fixture-v1")
        self.assertEqual(self.batch.provenance.extra["future_export_field"], {"kept": True})
        self.assertEqual(self.conversation.provenance.extra["account"], {"uuid": "anonymous-account"})
        self.assertEqual(main_message.provenance.extra["future_message_field"], {"kept": True})
        self.assertEqual(main_message.provenance.extra["raw_text"], "这是主分支。")
        self.assertIsNone(alt_message.updated_at)
        self.assertEqual(alt_message.provenance.extra["raw_updated_at"], "not-a-timestamp")

    def test_missing_parent_fields_form_a_linear_conversation(self):
        batch = parse_export({
            "uuid": "linear",
            "chat_messages": [
                {"uuid": "one", "sender": "human", "content": [{"type": "text", "text": "one"}]},
                {"uuid": "two", "sender": "assistant", "content": [{"type": "text", "text": "two"}]},
            ],
        })
        nodes = batch.conversations[0].nodes

        self.assertIsNone(nodes[0].parent_id)
        self.assertEqual(nodes[1].parent_id, "one")
        self.assertTrue(nodes[1].provenance.extra["parent_inferred"])

    def test_unknown_non_list_content_shape_is_preserved_as_a_block(self):
        batch = parse_export({
            "uuid": "future-content",
            "chat_messages": [{
                "uuid": "one",
                "sender": "assistant",
                "content": {"type": "future_container", "payload": [1, 2]},
            }],
        })
        block = batch.conversations[0].nodes[0].message.content[0]

        self.assertEqual(block.kind, "future_container")
        self.assertEqual(block.raw, {"type": "future_container", "payload": [1, 2]})

    def test_official_zip_entry_uses_claude_detector_and_indexes_other_files(self):
        archive = zip_fixture({
            "conversations.json": json.dumps(
                fixture("claude-conversations.json"), ensure_ascii=False
            ),
            "attachments/anonymous.txt": "anonymous attachment",
        })

        detected = detect_export(archive, "claude-data-export.zip")
        batch = parse_export(archive, source_name="claude-data-export.zip")
        container = batch.provenance.extra["container"]

        self.assertEqual(detected.format_id, "claude.conversations")
        self.assertEqual(container["json_entry"], "conversations.json")
        self.assertEqual(
            container["attachment_entries"],
            ["attachments/anonymous.txt"],
        )

    def test_official_account_folder_zip_is_detected_and_parsed(self):
        archive = zip_fixture({
            "anonymous@example.invalid/conversations.json": json.dumps(
                fixture("claude-official-variants.json"), ensure_ascii=False
            ),
            "anonymous@example.invalid/projects.json": "[]",
            "anonymous@example.invalid/users.json": "[]",
        })

        detected = detect_export(archive, "claude-data-export.zip")
        batch = parse_export(archive, source_name="claude-data-export.zip")

        self.assertEqual(detected.format_id, "claude.conversations")
        self.assertEqual(
            batch.provenance.extra["container"]["json_entry"],
            "anonymous@example.invalid/conversations.json",
        )
        self.assertEqual(
            batch.conversations[0].id,
            "claude-official-conversation-anonymous",
        )


class ImporterToleranceTests(unittest.TestCase):
    def test_bad_items_become_warnings_instead_of_aborting_valid_conversations(self):
        batch = parse_export([
            {
                "id": "partial",
                "title": "partial",
                "mapping": {
                    "valid": {"id": "valid", "parent": None, "children": [], "message": None},
                    "bad": "not-an-object",
                },
            },
            "not-a-conversation",
        ], format_hint="chatgpt")

        self.assertEqual(len(batch.conversations), 1)
        self.assertEqual([warning.code for warning in batch.warnings], ["invalid_node", "invalid_conversation"])

    def test_parser_is_deterministic_and_has_no_import_timestamp(self):
        payload = fixture("claude-conversations.json")
        first = parse_export(payload).model_dump(mode="json")
        second = parse_export(payload).model_dump(mode="json")

        self.assertEqual(first, second)
        self.assertNotIn("imported_at", first)


class KelivoBackupImporterTests(unittest.TestCase):
    def setUp(self):
        self.payload = fixture("kelivo-chats.json")
        self.batch = parse_export(self.payload, source_name="chats.json")
        self.conversation = self.batch.conversations[0]

    def test_detects_current_chat_backup_shape_and_keeps_message_order(self):
        detected = detect_export(self.payload, "chats.json")

        self.assertEqual(detected.format_id, "kelivo.chats_backup")
        self.assertEqual(self.batch.platform, "kelivo")
        self.assertEqual(
            [node.id for node in self.conversation.nodes],
            ["kelivo-user-1", "kelivo-assistant-v0", "kelivo-assistant-v1", "kelivo-user-2"],
        )

    def test_models_version_slots_as_branches_and_marks_selected_timeline(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        branches = {branch.leaf_node_id: branch for branch in self.conversation.branches}

        self.assertEqual(nodes["kelivo-assistant-v0"].parent_id, "kelivo-user-1")
        self.assertEqual(nodes["kelivo-assistant-v1"].parent_id, "kelivo-user-1")
        self.assertEqual(nodes["kelivo-user-2"].parent_id, "kelivo-assistant-v1")
        self.assertFalse(nodes["kelivo-assistant-v0"].message.metadata["source_version_selected"])
        self.assertTrue(nodes["kelivo-assistant-v1"].message.metadata["source_version_selected"])
        self.assertEqual(set(branches), {"kelivo-assistant-v0", "kelivo-user-2"})
        self.assertTrue(branches["kelivo-user-2"].is_current)

    def test_preserves_parts_attachments_tool_events_and_thought_signature(self):
        nodes = {node.id: node for node in self.conversation.nodes}
        first_blocks = nodes["kelivo-user-1"].message.content
        selected = nodes["kelivo-assistant-v1"].message
        final_blocks = nodes["kelivo-user-2"].message.content

        self.assertEqual([block.kind for block in first_blocks], ["text", "image", "future_part"])
        self.assertEqual(first_blocks[1].mime_type, "image/png")
        self.assertEqual(first_blocks[2].raw["payload"], "opaque-anonymous-payload")
        self.assertEqual(selected.metadata["thought_signature"], "anonymous-thought-signature")
        self.assertEqual(selected.content[-1].kind, "tool_event")
        self.assertTrue(selected.content[-1].raw["future_event_field"])
        self.assertEqual([block.kind for block in final_blocks], ["text", "file"])
        self.assertEqual(final_blocks[1].raw["name"], "second.txt")

    def test_keeps_unknown_and_unmatched_artifacts_in_provenance(self):
        nodes = {node.id: node for node in self.conversation.nodes}

        self.assertEqual(self.batch.provenance.extra["future_backup_field"], {"kept": True})
        self.assertIn("missing-message", self.batch.provenance.extra["unmatched_tool_events"])
        self.assertIn("missing-message", self.batch.provenance.extra["unmatched_thought_signatures"])
        self.assertEqual(self.conversation.provenance.extra["future_conversation_field"], {"kept": True})
        self.assertEqual(
            nodes["kelivo-assistant-v1"].message.provenance.extra["future_message_field"],
            ["kept"],
        )
        self.assertEqual(self.conversation.participants[0].id, "kelivo-assistant-anon")

    def test_detects_and_parses_root_session_json_zip(self):
        session = {
            "version": self.payload["version"],
            "conversation": self.payload["conversations"][0],
            "messages": self.payload["messages"],
            "toolEvents": self.payload["toolEvents"],
            "geminiThoughtSigs": self.payload["geminiThoughtSigs"],
            "attachments": [{
                "originalPath": "kelivo-file://upload/anonymous.txt",
                "zipPath": "attachments/upload/anonymous.txt",
                "type": "upload",
            }],
        }
        archive = zip_fixture({
            "session.json": json.dumps(session, ensure_ascii=False),
            "attachments/upload/anonymous.txt": "anonymous attachment",
        })

        detected = detect_export(archive, "kelivo-session.zip")
        batch = parse_export(archive, source_name="kelivo-session.zip")
        container = batch.provenance.extra["container"]
        selected = {
            node.id: node.message for node in batch.conversations[0].nodes
        }["kelivo-assistant-v1"]

        self.assertEqual(detected.format_id, "kelivo.chats_backup")
        self.assertEqual(container["json_entry"], "session.json")
        self.assertEqual(
            container["attachment_entries"],
            ["attachments/upload/anonymous.txt"],
        )
        self.assertEqual(
            batch.conversations[0].metadata["attachments"][0]["zipPath"],
            "attachments/upload/anonymous.txt",
        )
        self.assertEqual(selected.metadata["thought_signature"], "anonymous-thought-signature")
        self.assertEqual(selected.content[-1].kind, "tool_event")

    def test_parses_legacy_root_chats_json_zip(self):
        archive = zip_fixture({
            "chats.json": json.dumps(self.payload, ensure_ascii=False),
        })

        batch = parse_export(archive, source_name="kelivo-chats.zip")

        self.assertEqual(batch.provenance.extra["container"]["json_entry"], "chats.json")
        self.assertEqual(batch.conversations[0].id, "kelivo-conversation-anon-1")

    def test_uses_valid_root_payload_when_another_known_zip_entry_is_malformed(self):
        archive = zip_fixture({
            "session.json": "{anonymous-invalid-json",
            "chats.json": json.dumps(self.payload, ensure_ascii=False),
        })

        detected = detect_export(archive, "kelivo-chats.zip")
        batch = parse_export(archive, source_name="kelivo-chats.zip")

        self.assertEqual(detected.format_id, "kelivo.chats_backup")
        self.assertEqual(batch.provenance.extra["container"]["json_entry"], "chats.json")
        self.assertEqual(batch.conversations[0].id, "kelivo-conversation-anon-1")

    def test_malformed_json_items_warn_and_remain_in_provenance(self):
        payload = fixture("kelivo-malformed-items.json")

        batch = parse_export(payload, source_name="kelivo-malformed-items.json")
        conversation = batch.conversations[0]
        malformed = {node.id: node.message for node in conversation.nodes}[
            "kelivo-malformed-message-anon"
        ]
        warning_codes = {warning.code for warning in batch.warnings}

        self.assertEqual(len(batch.conversations), 1)
        self.assertEqual(len(conversation.nodes), 2)
        self.assertEqual([block.kind for block in malformed.content], ["unknown", "unknown"])
        self.assertEqual(
            malformed.content[0].raw,
            {"future_parts_shape": True},
        )
        self.assertEqual(
            malformed.content[1].raw,
            {"future_content_shape": [1, 2]},
        )
        self.assertTrue({
            "invalid_message",
            "invalid_conversation",
            "invalid_tool_events",
            "invalid_thought_signatures",
            "invalid_attachments",
            "invalid_version_selection",
            "invalid_message_parts",
            "invalid_message_content",
            "invalid_message_version",
        }.issubset(warning_codes))
        self.assertEqual(
            batch.provenance.extra["invalid_message_entries"][0]["value"],
            "anonymous-non-object-message",
        )
        self.assertEqual(
            batch.provenance.extra["invalid_conversation_entries"][0]["value"],
            "anonymous-non-object-conversation",
        )
        self.assertEqual(
            batch.provenance.extra["invalid_root_fields"]["attachments"],
            {"future_attachment_shape": True},
        )

    def test_parses_current_official_sqlite_backup_tables(self):
        archive = kelivo_sqlite_backup_fixture()

        detected = detect_export(archive, "kelivo-backup.zip")
        batch = parse_export(archive, source_name="kelivo-backup.zip")
        conversation = batch.conversations[0]
        messages = {node.id: node.message for node in conversation.nodes}
        assistant = messages["sqlite-assistant-anon-message"]
        container = batch.provenance.extra["container"]

        self.assertEqual(detected.format_id, "kelivo.chats_backup")
        self.assertEqual(container["database_entry"], "database/kelivo.db")
        self.assertEqual(container["sqlite"]["schema_version"], 1)
        self.assertIn("upload/anonymous.txt", container["attachment_entries"])
        self.assertEqual(conversation.metadata["source_message_ids"], [
            "sqlite-user-anon", "sqlite-assistant-anon-message",
        ])
        self.assertEqual(conversation.metadata["version_selections"], {
            "sqlite-assistant-group": 0,
        })
        self.assertEqual(
            [block.kind for block in assistant.content],
            ["reasoning", "text", "tool_call", "file", "tool_event"],
        )
        self.assertEqual(
            assistant.metadata["thought_signature"],
            "anonymous-sqlite-signature",
        )
        self.assertEqual(assistant.metadata["tool_events"][0]["id"], "anonymous-tool")
        self.assertEqual(
            assistant.provenance.extra["sqliteRow"]["message_order"],
            1,
        )
        artifact_kinds = {
            item["kind"] for item in assistant.provenance.extra["providerArtifacts"]
        }
        self.assertIn("future_provider_artifact", artifact_kinds)

    def test_malformed_sqlite_rows_warn_and_preserve_raw_parts(self):
        archive = kelivo_sqlite_backup_fixture(malformed_rows=True)

        batch = parse_export(archive, source_name="kelivo-backup.zip")
        conversation = batch.conversations[0]
        assistant = {
            node.id: node.message for node in conversation.nodes
        }["sqlite-assistant-anon-message"]
        warning_codes = {warning.code for warning in batch.warnings}
        malformed_parts = [
            block for block in assistant.content
            if block.kind in {"image", "tool_call"}
            and block.raw["payload"].startswith("anonymous-")
            or block.kind == "image" and block.raw["payload"].startswith("{")
        ]

        self.assertTrue({
            "invalid_version_selections",
            "invalid_tool_call_part",
            "invalid_message_part_payload",
        }.issubset(warning_codes))
        self.assertEqual(
            conversation.provenance.extra["sqliteRow"]["version_selections_json"],
            "{anonymous-invalid-json",
        )
        self.assertEqual(
            {block.kind for block in malformed_parts},
            {"image", "tool_call"},
        )
        self.assertEqual(
            batch.provenance.extra["sqliteSource"]["unparsedToolCallParts"][0]["payload"],
            "anonymous-not-a-json-object",
        )

    def test_rejects_unpublished_sqlite_schema_version(self):
        archive = kelivo_sqlite_backup_fixture(schema_version=2)

        self.assertEqual(
            detect_export(archive, "future-kelivo-backup.zip").format_id,
            "kelivo.chats_backup",
        )
        with self.assertRaisesRegex(
            KelivoSQLiteBackupUnsupported,
            r"expected published schemaVersion 1",
        ):
            parse_export(archive, source_name="future-kelivo-backup.zip")

    def test_does_not_guess_nested_session_json_path(self):
        session = {
            "version": self.payload["version"],
            "conversation": self.payload["conversations"][0],
            "messages": self.payload["messages"],
            "toolEvents": self.payload["toolEvents"],
            "geminiThoughtSigs": self.payload["geminiThoughtSigs"],
        }
        archive = zip_fixture({
            "nested/session.json": json.dumps(session, ensure_ascii=False),
        })

        with self.assertRaises(UnsupportedExportFormat):
            detect_export(archive, "unverified-layout.zip")


class AstrBotConversationImporterTests(unittest.TestCase):
    def setUp(self):
        self.raw = fixture_text("astrbot-conversations.jsonl")
        self.batch = parse_export(
            self.raw.encode("utf-8"),
            source_name="astrbot_conversations_export_20260826_090000.jsonl",
        )
        self.group = self.batch.conversations[0]
        self.private = self.batch.conversations[1]

    def test_detects_real_jsonl_text_and_preserves_one_conversation_per_line(self):
        detected = detect_export(self.raw, "astrbot_conversations_export.jsonl")

        self.assertEqual(detected.format_id, "astrbot.conversations_jsonl")
        self.assertEqual(self.batch.platform, "astrbot")
        self.assertEqual(len(self.batch.conversations), 2)
        self.assertEqual(self.group.id, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(self.private.id, "00000000-0000-0000-0000-000000000002")

    def test_preserves_platform_umo_persona_scope_without_inventing_group_members(self):
        context = self.group.metadata["source_context"]

        self.assertEqual(context["platform_id"], "anonymous-bot")
        self.assertEqual(context["user_id"], "anonymous-bot:GroupMessage:anonymous-group-session")
        self.assertEqual(context["persona_id"], "anonymous-persona")
        self.assertEqual(context["scope"], "group")
        self.assertFalse(context["participants_complete"])
        self.assertEqual(
            [(participant.kind, participant.id) for participant in self.group.participants],
            [
                ("source_session", "anonymous-bot:GroupMessage:anonymous-group-session"),
                ("persona", "anonymous-persona"),
            ],
        )
        self.assertFalse(self.group.participants[0].provenance.extra["is_sender_identity"])
        self.assertEqual(self.private.metadata["source_context"]["scope"], "private")
        self.assertEqual(len(self.private.participants), 1)

    def test_keeps_message_order_thinking_media_tools_checkpoint_and_unknown_raw(self):
        messages = [node.message for node in self.group.nodes]

        self.assertEqual([message.role for message in messages], ["user", "assistant", "assistant", "tool", "unknown"])
        self.assertEqual([block.kind for block in messages[0].content], ["text", "image_url"])
        self.assertEqual([block.kind for block in messages[1].content], ["think", "text", "future_content_block"])
        self.assertEqual(messages[1].content[0].raw["encrypted"], "anonymous-signature")
        self.assertEqual(messages[1].content[2].raw["payload"], {"anonymous": True})
        self.assertEqual(messages[1].author.id, "anonymous-persona")
        self.assertEqual(messages[2].content[0].kind, "tool_call")
        self.assertEqual(messages[3].metadata["source_tool_call_id"], "anonymous-tool-call")
        self.assertEqual(messages[4].content[0].kind, "_checkpoint")
        self.assertEqual(messages[4].content[0].raw, {"id": "anonymous-checkpoint"})
        self.assertEqual(messages[4].provenance.extra["raw_content"], {"id": "anonymous-checkpoint"})

    def test_bad_jsonl_line_is_a_warning_and_valid_lines_still_parse(self):
        batch = parse_export("{bad-jsonl-line\n" + self.raw, source_name="export.jsonl")

        self.assertEqual(len(batch.conversations), 2)
        self.assertEqual(batch.warnings[0].code, "invalid_jsonl_line")

    def test_detects_official_dashboard_api_envelope_and_decodes_history(self):
        payload = fixture("astrbot-dashboard-response.json")

        detected = detect_export(payload, "astrbot-conversations-api.json")
        batch = parse_export(payload, source_name="astrbot-conversations-api.json")
        valid, malformed = batch.conversations

        self.assertEqual(detected.format_id, "astrbot.conversations_jsonl")
        self.assertEqual([node.message.role for node in valid.nodes], ["user", "assistant"])
        self.assertEqual(
            [block.kind for block in valid.nodes[1].message.content],
            ["text", "future_part"],
        )
        self.assertEqual(valid.metadata["source_history_field"], "history")
        self.assertEqual(malformed.nodes, [])
        self.assertEqual([warning.code for warning in batch.warnings], ["invalid_history_json"])
        self.assertEqual(malformed.provenance.extra["raw_history"], "{not-json")
        self.assertEqual(
            malformed.provenance.extra["future_conversation_field"],
            {"kept": True},
        )

    def test_malformed_official_content_is_detected_warned_and_preserved(self):
        payload = {
            "cid": "00000000-0000-0000-0000-000000000005",
            "user_id": "anonymous-api:FriendMessage:anonymous-session",
            "platform_id": "anonymous-api",
            "content": {"future_history_shape": [1, 2]},
        }

        detected = detect_export(payload, "astrbot_conversations_export.jsonl")
        batch = parse_export(payload, source_name="astrbot_conversations_export.jsonl")

        self.assertEqual(detected.format_id, "astrbot.conversations_jsonl")
        self.assertEqual(len(batch.conversations), 1)
        self.assertEqual(batch.conversations[0].nodes, [])
        self.assertEqual([warning.code for warning in batch.warnings], ["invalid_content"])
        self.assertEqual(
            batch.conversations[0].provenance.extra["raw_content"],
            {"future_history_shape": [1, 2]},
        )

    def test_official_detail_response_derives_platform_from_umo(self):
        payload = {
            "status": "ok",
            "message": None,
            "data": {
                "user_id": "anonymous-detail:FriendMessage:anonymous-session",
                "cid": "00000000-0000-0000-0000-000000000006",
                "title": "Anonymous detail response",
                "persona_id": None,
                "history": "[]",
                "created_at": 1760000500,
                "updated_at": 1760000560,
            },
        }

        batch = parse_export(payload, source_name="astrbot-conversation-detail.json")
        conversation = batch.conversations[0]

        self.assertEqual(conversation.metadata["source_platform_id"], "anonymous-detail")
        self.assertEqual(conversation.participants[0].platform_id, "anonymous-detail")
        self.assertNotIn("missing_platform_id", [warning.code for warning in batch.warnings])

    def test_decoded_record_list_is_supported_and_deterministic(self):
        decoded = [json.loads(line) for line in self.raw.splitlines() if line.strip()]
        first = parse_export(decoded, format_hint="astrbot").model_dump(mode="json")
        second = parse_export(decoded, format_hint="astrbot").model_dump(mode="json")

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
