from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, channel="msedge")
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.request.post("http://127.0.0.1:8876/api/providers", data={"name":"分级线路","protocol":"openai","base_url":"https://example.com/v1","model":"grade-model","api_key":"test"})
    page.request.post("http://127.0.0.1:8876/api/memories", data={"title":"旧记忆","content":"需要重新分级","kind":"event","persona_key":"__unassigned__","importance":.5})
    page.goto("http://127.0.0.1:8876/?memory-regrade-smoke=1")
    page.wait_for_load_state("networkidle")
    page.evaluate("openSettings('memory')")
    page.locator("#regradeMemories").click()
    page.locator("#memoryRegradeDialog:not([hidden])").wait_for()
    assert page.locator("#memoryRegradeProvider option").count() >= 1
    assert "每批最多 80 条" in page.locator("#memoryRegradeStatus").inner_text()
    assert not errors, errors

    android = browser.new_page(viewport={"width": 390, "height": 844})
    android_errors = []
    android.on("pageerror", lambda error: android_errors.append(str(error)))
    android.add_init_script("""
      localStorage.setItem('atherloom:providers', JSON.stringify([{id:'android-grade',name:'安卓分级线路',protocol:'openai',base_url:'https://example.com/v1',model:'grade-model',api_key:'test',enabled:true,max_tokens:4096}]));
      localStorage.setItem('atherloom:settings', JSON.stringify({tool_permissions:{memory_read:'allow',memory_write:'allow'}}));
      localStorage.setItem('atherloom:memories', JSON.stringify([{id:'old-memory',title:'长期约定',content:'每年生日一起吃蛋糕',kind:'promise',persona_key:'__default__',importance:.5,confidence:1,strength:.65,memory_status:'active',source_type:'explicit',created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z'}]));
    """)
    android.route("https://example.com/v1/chat/completions", lambda route: route.fulfill(status=200, content_type="application/json", body='{"choices":[{"message":{"content":"[{\\"memory_id\\":\\"old-memory\\",\\"importance\\":0.9,\\"reason\\":\\"长期重要承诺\\"}]"}}]}'))
    android.goto("http://127.0.0.1:8876/?standalone=1&memory-regrade-android-smoke=1")
    android.wait_for_load_state("networkidle")
    android.evaluate("openSettings('memory')")
    android.locator("#regradeMemories").click()
    android.locator("#previewMemoryRegrade").click()
    android.wait_for_timeout(2500)
    assert android.locator("[data-regrade-id='old-memory']").count(), android.locator("#memoryRegradeStatus").inner_text()
    assert "重要 0.9" in android.locator("#memoryRegradeResults").inner_text()
    android.locator("#applyMemoryRegrade").click()
    android.wait_for_function("JSON.parse(localStorage.getItem('atherloom:memories'))[0].importance === 0.9")
    assert not android_errors, android_errors
    browser.close()
