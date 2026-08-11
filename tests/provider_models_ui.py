import sys
from playwright.sync_api import sync_playwright

base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8876"
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    for round_number in range(1, 4):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(f"{base_url}/?provider-models-round={round_number}", wait_until="networkidle")
        result = page.evaluate("""
        async roundNumber => {
          const form = document.querySelector('#providerForm');
          form.hidden = false;
          form.elements.name.value = `多模型线路 ${roundNumber}`;
          form.elements.protocol.value = 'deepseek';
          form.elements.base_url.value = 'https://api.deepseek.com/v1';
          form.elements.api_key.value = 'test-key';
          form.elements.model.value = 'deepseek-v4-flash';
          addProviderModel();
          form.elements.model.value = 'deepseek-v4-pro';
          addProviderModel();
          const payload = providerFormData(form);
          const saved = await api('/api/providers', {method:'POST', body:JSON.stringify(payload)});
          state.providers.push(saved);
          renderSettings();
          return {models:saved.models, tags:[...document.querySelectorAll('.provider-model-tags span')].map(x=>x.textContent)};
        }
        """, round_number)
        assert set(result["models"]) == {"deepseek-v4-flash", "deepseek-v4-pro"}, result
        assert "deepseek-v4-flash" in result["tags"] and "deepseek-v4-pro" in result["tags"], result
        assert not errors, errors
        print(f"PASS round {round_number}: one API line saved and rendered two models")
        page.close()
    browser.close()
