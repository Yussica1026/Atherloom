import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/assets/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/assets/app.css").read_text(encoding="utf-8")


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

    def test_title_dropdown_delete_does_not_depend_on_confirm(self):
        self.assertIn('updateHistoryState(button.dataset.deleteSwitch,"delete",{skipConfirm:true})', APP)
        self.assertIn('if(!skipConfirm&&!confirm(', APP)

    def test_conversation_rows_support_long_press_delete(self):
        self.assertIn("bindConversationLongPress", APP)
        self.assertIn('classList.add("delete-revealed")', APP)
        self.assertIn('addEventListener("contextmenu",event=>event.preventDefault())', APP)
        self.assertIn('Math.hypot(event.clientX-startX,event.clientY-startY)>12', APP)
        self.assertIn('.conversation-switch-row.delete-revealed .conversation-switch-delete{display:grid}', CSS)

    def test_reasoning_is_folded_by_default(self):
        self.assertIn("<details class=\"thinking\"><summary>思考过程（点开查看）", APP)
        self.assertNotIn('<details class="thinking" open>', APP)

    def test_browser_backup_reports_download_record(self):
        self.assertIn("具体位置请查看浏览器下载记录", APP)

    def test_versioned_script_is_current(self):
        self.assertIn('assets/app.js?v=0535', INDEX)

    def test_chat_enter_is_newline_only(self):
        self.assertNotIn('$("#prompt").addEventListener("keydown"', APP)

    def test_android_pdf_is_blocked_before_reading_bytes(self):
        guard = APP.index("if(declaredPdf&&window.AtherloomNative)")
        read = APP.index("file.slice(0,limit).arrayBuffer()")
        self.assertLess(guard, read)

    def test_roleplay_opens_new_setup_without_forcing_active_story(self):
        self.assertIn('roleplayState.stories=await api("/api/roleplay/stories");renderRoleplayStories();', APP)
        self.assertIn('resetRoleplaySetup();\n  $(".roleplay-desk").scrollTop=0;', APP)
        self.assertNotIn('const active=roleplayState.stories.find(story=>story.status==="active")', APP)

    def test_http_errors_have_status_specific_explanations(self):
        for status in (400, 401, 402, 403, 429, 500):
            self.assertIn(f'{status}:', APP)
        self.assertNotIn('assistant.content=`连接失败：', APP)


if __name__ == "__main__":
    unittest.main()
