import sys

from playwright.sync_api import sync_playwright


base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8876"

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    for round_number in range(1, 4):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(f"{base_url}/?token-ui-round={round_number}", wait_until="networkidle")
        result = page.evaluate(
        """
        () => {
          const previous = {
            messages: state.messages,
            providers: state.providers,
            provider: state.provider,
            conversations: state.conversations,
            current: state.current,
          };
          state.providers = [{id: "token-ui", name: "Token 测试", model: "claude-opus-4-6"}];
          state.provider = "token-ui";
          state.conversations = [{id: "token-conversation", provider_id: "token-ui", persona_id: null}];
          state.current = "token-conversation";
          state.messages = [{
            role: "assistant",
            content: "缓存用量显示测试",
            model: "claude-opus-4-6",
            usage: {
              input_tokens: 9,
              output_tokens: 4,
              cache_creation_input_tokens: 33601,
              cache_read_input_tokens: 0,
              total_tokens: 33614,
            },
          }];
          renderMessages();
          renderPickers();
          const picker = document.querySelector("#modelPicker")?.textContent || "";
          const meta = document.querySelector(".message.assistant .message-meta")?.textContent || "";
          state.messages = previous.messages;
          state.providers = previous.providers;
          state.provider = previous.provider;
          state.conversations = previous.conversations;
          state.current = previous.current;
          return {picker, meta};
        }
        """
        )
        assert "33,614 全部 tokens" in result["picker"], result
        assert "33,614 全部 tokens" in result["meta"], result
        settings_result = page.evaluate(
            """
            async () => {
              openSettings("tools");
              const timeout = document.querySelector("#toolTimeoutSeconds");
              const permission = document.querySelector('[data-permission="life_records"]');
              timeout.value = "240";
              const payload = appSettingsPayload();
              const saved = await persistAppSettingsNow();
              return {
                toolsTab: document.querySelector("#tab-tools").classList.contains("active"),
                permission: permission.value,
                timeout: payload.tool_timeout_seconds,
                savedTimeout: saved.tool_timeout_seconds,
              };
            }
            """
        )
        assert settings_result["toolsTab"] is True, settings_result
        assert settings_result["permission"] == "allow", settings_result
        assert settings_result["timeout"] == 240, settings_result
        assert not errors, errors
        inline_page = browser.new_page(viewport={"width": 390, "height": 844})
        inline_errors = []
        inline_page.on("console", lambda message: inline_errors.append(message.text) if message.type == "error" else None)
        inline_page.goto(f"{base_url}/inline.html?tool-ui-round={round_number}", wait_until="networkidle")
        assert inline_page.locator("#toolTimeoutSeconds").count() == 1
        assert inline_page.locator('[data-permission="life_records"]').count() == 1
        assert not inline_errors, inline_errors
        inline_page.close()
        print(f"PASS round {round_number}: tokens, life-tool permission and tool timeout setting are active")
        page.close()
    browser.close()
