import sys
from playwright.sync_api import sync_playwright

base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8876"

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    for round_number in range(1, 4):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(f"{base_url}/?life-book-round={round_number}", wait_until="networkidle")
        page.locator("#mobileMenu").click()
        page.locator("#openLifeBook").click()
        page.wait_for_selector("#lifeBookSpace:not([hidden])")
        assert page.get_by_role("button", name="纪念日", exact=True).count() == 1
        page.get_by_role("button", name="纪念日", exact=True).click()
        page.locator('#anniversaryForm input[name="title"]').fill(f"相识纪念日 {round_number}")
        page.locator('#anniversaryForm input[name="occurred_at"]').fill("2026-08-20")
        page.locator("#anniversaryForm button.primary").click()
        page.wait_for_selector(f"text=相识纪念日 {round_number}")
        page.get_by_role("button", name="备忘录", exact=True).click()
        page.locator('#memoForm input[name="title"]').fill(f"取快递 {round_number}")
        page.locator('#memoForm input[name="occurred_at"]').fill("2026-08-20T09:30")
        page.locator("#memoForm button.primary").click()
        card = page.locator(".life-date-card", has_text=f"取快递 {round_number}")
        card.get_by_role("button", name="完成").click()
        page.wait_for_selector(".life-date-card.is-done")
        page.get_by_role("button", name="倒数日", exact=True).click()
        page.locator('#countdownForm input[name="title"]').fill(f"出发旅行 {round_number}")
        page.locator('#countdownForm input[name="occurred_at"]').fill("2026-08-30")
        page.locator("#countdownForm button.primary").click()
        page.wait_for_selector(f"text=出发旅行 {round_number}")
        assert not errors, errors
        page.locator("#closeLifeBook").click()
        assert page.locator("#lifeBookSpace").is_hidden()
        print(f"PASS round {round_number}: life book create, memo toggle, countdown and close")
        page.close()
    browser.close()
