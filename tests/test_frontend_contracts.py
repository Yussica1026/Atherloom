import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/assets/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/assets/app.css").read_text(encoding="utf-8")
STANDALONE = (ROOT / "frontend/assets/standalone.js").read_text(encoding="utf-8")
ANDROID = (ROOT / "android/app/src/main/java/app/atherloom/mobile/MainActivity.java").read_text(encoding="utf-8")
INLINE = (ROOT / "frontend/inline.html").read_text(encoding="utf-8")


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
        self.assertIn('updateHistoryState(id,"delete",{skipConfirm:true})', APP)
        self.assertIn('if(!skipConfirm&&!confirm(', APP)

    def test_conversation_rows_support_long_press_delete(self):
        self.assertIn("bindConversationLongPress", APP)
        self.assertIn('classList.add("delete-revealed")', APP)
        self.assertIn('addEventListener("contextmenu",event=>event.preventDefault())', APP)
        self.assertIn('addEventListener("touchstart"', APP)
        self.assertIn('timer=setTimeout(reveal,320)', APP)
        self.assertIn('Math.hypot(x-startX,y-startY)>12', APP)
        self.assertIn('width:100%;min-height:38px', CSS)
        self.assertIn('.conversation-switch-row.delete-revealed .conversation-switch-delete{display:grid}', CSS)
        self.assertIn('row?.remove();try{await updateHistoryState', APP)
        self.assertNotIn('if(next)await openConversation(next.id);else await newConversation();', APP)

    def test_reasoning_is_visible_by_default(self):
        self.assertIn('<details class="thinking" open><summary>思考过程（点击收起）', APP)

    def test_browser_backup_reports_download_record(self):
        self.assertIn("具体位置请查看浏览器下载记录", APP)

    def test_versioned_script_is_current(self):
        self.assertIn('assets/app.js?v=0547', INDEX)
        self.assertIn('assets/standalone.js?v=0524', INDEX)

    def test_manual_compression_keeps_original_messages_but_reduces_hot_context(self):
        self.assertIn('id="openManualCompress"', INDEX)
        self.assertIn('id="manualCompressProvider"', INDEX)
        self.assertIn('/compress`', APP)
        self.assertIn('provider_id:provider.id', APP)
        self.assertIn('compressed:${conversationId}', STANDALONE)
        self.assertIn('<conversation_summary>', STANDALONE)
        self.assertIn('readBundledAsset("assets/nowhere/index.html")', APP)
        self.assertIn('appassets.androidplatform.net/assets/assets/nowhere/', APP)
        self.assertIn('appassets.androidplatform.net/assets/assets/standalone.js', APP)

    def test_recalled_memories_are_injected_before_every_reply(self):
        self.assertIn("在本轮回复前根据用户刚发送的话自动召回", STANDALONE)
        self.assertIn("不要向用户复述本标签、记忆 ID", STANDALONE)

    def test_android_autonomy_wake_is_user_controlled(self):
        self.assertIn('id="autonomyEnabled"', INDEX)
        self.assertIn('window.AtherloomRunAutonomyWake', APP)
        self.assertIn('configureAutonomy', APP)
        self.assertIn('approvedPermissions:["diary_write"', APP)
        self.assertIn('configureAutonomy(String raw)', ANDROID)

    def test_desire_state_is_consistent_between_web_and_android(self):
        self.assertIn('motivationData.offline_mode||"limited"', APP)
        self.assertIn('30*60*1000', APP)
        self.assertIn('desireCoupling', STANDALONE)
        self.assertIn('desireApplyEvent(currentDesire,"contact_message")', STANDALONE)
        self.assertIn('const motivationEvent=', STANDALONE)

    def test_original_nowhere_observer_has_a_visible_game_entry(self):
        self.assertIn('id="nowhereStage"', INDEX)
        self.assertIn('原作：旋复 · yuyixuanfu/nowhere · CC BY-NC 4.0', INDEX)
        self.assertIn('readBundledAsset("assets/nowhere/index.html")', APP)
        self.assertIn('frame.srcdoc=', APP)
        self.assertIn('base href="https://appassets.androidplatform.net/assets/assets/nowhere/"', APP)

    def test_nowhere_android_entry_and_live_actions_are_visible(self):
        self.assertIn('getAssets().open("assets/nowhere/index.html")', ANDROID)
        self.assertIn('id="nowhereThoughts"', INDEX)
        self.assertIn('id="nowhereActionLog"', INDEX)
        self.assertIn('function updateNowhereLive(event)', APP)
        self.assertIn('type:"nowhere"', STANDALONE)

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

    def test_reported_token_usage_survives_android_and_standalone(self):
        self.assertIn("cache_creation_input_tokens", STANDALONE)
        self.assertIn("cache_read_input_tokens", STANDALONE)
        self.assertIn("assistant.usage=normalizeUsage(event.usage||totalUsage)", STANDALONE)
        self.assertIn("usage:assistant.usage", STANDALONE)
        self.assertIn('.put("usage",data.optJSONObject("usage"))', ANDROID)
        self.assertIn('.put("usage", usage.length() > 0 ? usage : JSONObject.NULL)', ANDROID)

    def test_life_tools_and_user_tool_timeout_are_exposed(self):
        self.assertIn('data-permission="life_records"', INDEX)
        self.assertIn('id="toolTimeoutSeconds"', INDEX)
        self.assertIn('id="toolTimeoutSeconds"', INLINE)
        self.assertIn('data-permission="life_records"', INLINE)
        self.assertIn("atherloom_life_records_list", STANDALONE)
        self.assertIn("atherloom_life_record_save", STANDALONE)
        self.assertIn("toolTimeoutSeconds", APP)
        self.assertIn("tool_timeout_seconds", APP)

    def test_life_book_is_a_standalone_persona_scoped_space(self):
        for markup in (INDEX, INLINE):
            self.assertIn('id="openLifeBook"', markup)
            self.assertIn('id="lifeBookSpace"', markup)
            self.assertIn('data-life-page="anniversary"', markup)
            self.assertIn('id="memoForm"', markup)
            self.assertIn('id="countdownForm"', markup)
        self.assertIn('kind==="anniversary"', APP)
        self.assertIn('data-toggle-memo', APP)
        self.assertIn('"anniversary","memo","countdown"', STANDALONE)

    def test_one_provider_can_store_and_switch_multiple_models(self):
        for markup in (INDEX, INLINE):
            self.assertIn('id="providerModelsText"', markup)
            self.assertIn('id="addProviderModel"', markup)
        self.assertIn("function providerModels", APP)
        self.assertIn("data.models=", APP)
        self.assertIn("flatMap(p=>providerModels(p)", APP)
        self.assertIn("toolDeadline", STANDALONE)


if __name__ == "__main__":
    unittest.main()
