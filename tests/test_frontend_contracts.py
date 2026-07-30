import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/assets/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


class FrontendRegressionContracts(unittest.TestCase):
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

    def test_browser_backup_reports_download_record(self):
        self.assertIn("具体位置请查看浏览器下载记录", APP)

    def test_versioned_script_is_current(self):
        self.assertIn('assets/app.js?v=0531', INDEX)


if __name__ == "__main__":
    unittest.main()
