import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8876/?question-touch=1"


with sync_playwright() as playwright:
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=str(edge) if edge.exists() else None,
    )
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=3,
        has_touch=True,
        is_mobile=True,
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(URL, wait_until="networkidle")
    page.evaluate("() => window.dismissLaunchScreen?.()")
    page.evaluate(
        """
        () => {
          state.messages = [{
            id: "question-touch-smoke",
            role: "assistant",
            content: `挑一个。<questions>[{"question":"想先从哪个开始？","options":["药浴——艾草味道的温水里慢慢来","审讯室反转——我绑着你审","档案室——深夜查档","舞台后台——你吹完中场，我调音"]}]</questions>`,
            model: "deepseek-v4-flash"
          }];
          renderMessages();
        }
        """
    )
    option = page.get_by_role("button", name="药浴——艾草味道的温水里慢慢来")
    option.scroll_into_view_if_needed()
    box = option.bounding_box()
    assert box, "question option has no touch target"
    center = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
    # Reproduce Android WebViews that deliver touch events but never synthesize click.
    option.evaluate("element => { element.onclick = null; }")
    page.touchscreen.tap(center["x"], center["y"])
    page.wait_for_timeout(250)
    draft = page.locator("#prompt").input_value()
    selected = option.evaluate("element => element.classList.contains('selected')")
    assert "药浴" in draft, f"touch did not update the composer: {draft!r}"
    assert selected, "touch did not expose the selected state"
    assert option.get_attribute("aria-pressed") == "true"
    assert page.locator("#send").is_enabled(), "choice did not enable send"
    status = page.locator(".question-selection-status")
    assert status.is_visible() and "点右侧发送" in status.inner_text()

    # A second selection replaces the first answer instead of duplicating the question.
    replacement = page.get_by_role("button", name="审讯室反转——我绑着你审")
    replacement_box = replacement.bounding_box()
    assert replacement_box
    page.touchscreen.tap(
        replacement_box["x"] + replacement_box["width"] / 2,
        replacement_box["y"] + replacement_box["height"] / 2,
    )
    page.wait_for_timeout(150)
    draft = page.locator("#prompt").input_value()
    assert "审讯室反转" in draft and "药浴" not in draft
    assert draft.count("关于「想先从哪个开始？」") == 1
    assert replacement.get_attribute("aria-pressed") == "true"
    assert not errors, f"browser errors: {errors}"
    print("PASS mobile question choices accept real touch and replace visibly")
    context.close()
    browser.close()
