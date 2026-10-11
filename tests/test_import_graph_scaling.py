from __future__ import annotations

import unittest

from backend.importers.chatgpt import ChatGPTConversationsImporter


class ImportGraphScalingTests(unittest.TestCase):
    def test_long_linear_conversation_does_not_hit_recursion_limit(self) -> None:
        message_count = 3000
        mapping: dict[str, object] = {}
        for index in range(message_count):
            node_id = f"node-{index}"
            parent_id = f"node-{index - 1}" if index else None
            mapping[node_id] = {
                "id": node_id,
                "parent": parent_id,
                "children": [f"node-{index + 1}"] if index + 1 < message_count else [],
                "message": {
                    "id": f"message-{index}",
                    "author": {"role": "user" if index % 2 == 0 else "assistant"},
                    "content": {"content_type": "text", "parts": [str(index)]},
                },
            }

        batch = ChatGPTConversationsImporter().parse([{
            "id": "long-chat",
            "title": "long",
            "mapping": mapping,
            "current_node": f"node-{message_count - 1}",
        }])

        conversation = batch.conversations[0]
        self.assertEqual(message_count, len(conversation.nodes))
        self.assertEqual(1, len(conversation.branches))
        self.assertEqual(message_count, len(conversation.branches[0].node_ids))
        self.assertTrue(conversation.branches[0].is_current)


if __name__ == "__main__":
    unittest.main()
