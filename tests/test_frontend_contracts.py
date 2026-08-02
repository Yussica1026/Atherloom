import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/assets/app.js").read_text(encoding="utf-8")
STANDALONE = (ROOT / "frontend/assets/standalone.js").read_text(encoding="utf-8")
JAVA = (ROOT / "android/app/src/main/java/app/atherloom/mobile/MainActivity.java").read_text(encoding="utf-8")


class AndroidFrontendRegressionContracts(unittest.TestCase):
    def test_mobile_hub_toggle_does_not_close_sidebar(self):
        self.assertIn(".new-chat:not(.sidebar-hub-toggle)", APP)

    def test_unfinished_card_room_is_hidden(self):
        self.assertIn('game.id!=="card_room"', APP)

    def test_book_reader_rejects_binary_e_books_and_numeric_garbage(self):
        self.assertIn('return "zip"', APP)
        self.assertIn('return "mobi"', APP)
        self.assertIn("validateBookText(text)", APP)
        self.assertIn("numbers/visible>.45", APP)

    def test_conversation_action_stops_row_click(self):
        self.assertIn("event.stopPropagation();updateHistoryState", APP)

    def test_reasoning_is_folded_by_default(self):
        self.assertIn("<details class=\"thinking\"><summary>思考过程（点开查看）", APP)
        self.assertNotIn('<details class="thinking" open>', APP)

    def test_android_backup_writes_download_and_returns_location(self):
        self.assertIn('@JavascriptInterface public String saveBackup', JAVA)
        self.assertIn('Environment.DIRECTORY_DOWNLOADS + "/Atherloom"', JAVA)
        self.assertIn('"location"', JAVA)

    def test_android_continuity_archives_before_trimming_and_keeps_threads(self):
        self.assertIn("const syncContinuity=", STANDALONE)
        self.assertIn('kind:"timeline"', STANDALONE)
        self.assertLess(STANDALONE.index('write("memories",[memory'), STANDALONE.index('write(`timeline-archived:${conversationId}`'))
        self.assertIn("<open_threads>", STANDALONE)
        self.assertIn("hotMessages(body.conversation_id,history)", STANDALONE)

    def test_android_memory_recall_has_honesty_boundary_and_use_weight(self):
        self.assertIn("semantic>=.42", STANDALONE)
        self.assertIn("item.recall_count=Number(item.recall_count||0)+1", STANDALONE)
        self.assertIn("greeting?rows.sort", STANDALONE)
        self.assertNotIn("semantic>=.2", STANDALONE)

    def test_android_message_changes_invalidate_timeline(self):
        self.assertIn("const invalidateContinuity=", STANDALONE)
        self.assertGreaterEqual(STANDALONE.count("invalidateContinuity(conversation.id)"), 3)


if __name__ == "__main__":
    unittest.main()
