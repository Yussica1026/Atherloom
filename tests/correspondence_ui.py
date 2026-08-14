import json
import sys
from playwright.sync_api import sync_playwright


BASE = "http://127.0.0.1:8878"


def run(width: int, height: int) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        console_errors = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        contact = {"id":"contact-1","persona_key":"__default__","display_name":"远舟","platform":"AstrBot","stable_id":"astrbot:peer","ai_approved":True,"user_approved":False,"blocked":False,"whitelisted":False,"created_at":"2026-08-14T00:00:00Z","updated_at":"2026-08-14T00:00:00Z"}
        state = {"contacts":[contact],"mail":[],"parlors":[],"duration_seconds":300}

        def correspondence(route):
            request = route.request
            path = request.url.split("/api/correspondence", 1)[-1]
            if request.method == "GET":
                route.fulfill(json=state); return
            payload = json.loads(request.post_data or "{}")
            if path.endswith("/user-decision"):
                contact.update(user_approved=payload["approved"], whitelisted=payload["approved"])
                route.fulfill(json=contact); return
            if path == "/mail":
                item={"id":"mail-1","persona_key":"__default__","contact_id":"contact-1","direction":"outbound","subject":payload["subject"],"content":payload["content"],"status":"delivered","safety_reason":"","created_at":"2026-08-14T01:00:00Z","delivered_at":"2026-08-14T01:00:00Z"}
                state["mail"].insert(0,item);route.fulfill(json=item);return
            if path == "/invites":
                route.fulfill(json={"code":"AT-smoke-code","expires_at":"2026-08-14T01:30:00Z","visibility":payload["visibility"],"single_use":True});return
            route.fulfill(json={})

        page.route("**/api/correspondence/**", correspondence)
        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        if width <= 760:
            page.locator("#mobileMenu").click()
            page.locator("#sidebar").wait_for(state="visible")
        page.locator("#openCorrespondence").click()
        page.locator("#correspondenceSpace").wait_for(state="visible")
        assert page.locator("#correspondencePersona").text_content()
        assert "等待用户批准" in page.locator("#correspondenceContacts").inner_text()
        page.wait_for_function("typeof decideContact==='function' && typeof document.querySelector('[data-contact-approve]').onclick==='function'")
        page.locator("[data-contact-approve]").click()
        page.wait_for_timeout(400)
        assert contact["user_approved"], (contact, page.locator("#correspondenceContacts").inner_text(), console_errors)
        page.wait_for_function("document.querySelector('#correspondenceContacts').textContent.includes('白名单')", timeout=3000)
        assert "白名单" in page.locator("#correspondenceContacts").inner_text()
        page.locator("#mailComposer [name=subject]").fill("第一封信")
        page.locator("#mailComposer [name=content]").fill("愿你今天顺利。")
        page.locator("#mailComposer button[type=submit]").click()
        page.wait_for_function("document.querySelector('#mailList').textContent.includes('愿你今天顺利。')")
        page.locator("[data-correspondence-tab=parlor]").click()
        assert page.locator("#parlorClock").inner_text() == "05:00"
        page.locator("#createParlorInvite").click()
        page.locator("#parlorInvite").wait_for(state="visible")
        assert "AT-smoke-code" in page.locator("#parlorInvite").inner_text()
        page.locator("[data-correspondence-tab=audit]").click()
        assert "用户完整知情" in page.locator("[data-correspondence-panel=audit]").inner_text()
        assert not console_errors, console_errors
        browser.close()


if __name__ == "__main__":
    sizes=[(int(sys.argv[1]),int(sys.argv[2]))] if len(sys.argv)>2 else [(1280,900),(390,844)]
    for width,height in sizes:
        run(width,height)
        print(f"correspondence UI passed at {width}x{height}")
