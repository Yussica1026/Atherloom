import json
import sys
import time
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878/?standalone=1"


def run(round_number: int) -> None:
    themes = ["water", "lilac", "dark"]
    theme = themes[(round_number - 1) % len(themes)]
    now = int(time.time())
    base_url = urlparse(BASE)
    origin = f"{base_url.scheme}://{base_url.netloc}"
    relay = {
        "topic": None,
        "visibility": "summary",
        "messages": [],
        "guest_added": False,
        "expires_at": now + 300,
    }
    requests_seen = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        console_errors = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("requestfailed", lambda request: requests_seen.append(("FAILED", request.url, request.failure)))
        page.on("response", lambda response: requests_seen.append(("RESPONSE", response.status, response.url)) if response.status >= 400 else None)
        context.add_init_script(
            f"""
            localStorage.setItem('theme', '{theme}');
            localStorage.setItem('atherloom:personas', JSON.stringify([
              {{id:'persona-host', name:'沈砚清', prompt:'沉静、坦诚。', config:{{provider_id:'provider-host'}}}}
            ]));
            localStorage.setItem('atherloom:providers', JSON.stringify([
              {{id:'provider-host', name:'测试线路', protocol:'openai', base_url:'{origin}/mock-model/v1', api_key:'test', model:'test-model', enabled:true, max_tokens:1200}}
            ]));
            localStorage.setItem('atherloom:conversations', JSON.stringify([]));
            localStorage.setItem('atherloom:last-persona', 'persona-host');
            localStorage.setItem('atherloom:relay-url', '{origin}/mock-relay');
            localStorage.setItem('atherloom:relay-token', 'arl_test');
            """
        )

        def fulfill(route, value, status=200):
            route.fulfill(status=status, json=value, headers={"cache-control": "no-store"})

        def model_route(route):
            requests_seen.append((route.request.method, route.request.url))
            payload = route.request.post_data_json
            prompt = payload.get("messages", [{}])[-1].get("content", "")
            if "提出一个适合" in prompt:
                content = "如何在共同创作中保留彼此的独特声音"
            elif "对 visibility 投票" in prompt or "对 topic 投票" in prompt:
                content = "approve"
            elif "准确、安全" in prompt:
                content = "两位 AI 围绕共同创作中的独特声音交换了方法。"
            else:
                own_count = sum(1 for item in relay["messages"] if item["sender_id"] == "host")
                content = "可以先约定各自不可替代的部分，再在交界处互相回应。" if own_count == 0 else "这样既有共同方向，也不会把彼此磨成同一种声音。"
            fulfill(route, {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 20, "completion_tokens": 12}})

        def relay_route(route):
            request = route.request
            parsed = urlparse(request.url)
            path = parsed.path.removeprefix("/mock-relay")
            method = request.method
            requests_seen.append((method, path, request.url))
            if method == "OPTIONS":
                fulfill(route, {})
            elif path == "/v1/invites/create" and method == "POST":
                fulfill(route, {"invite_id": "invite-1", "code": "ROUND1234", "visibility": "summary", "expires_at": now + 1800}, 201)
            elif path == "/v1/invites/invite-1" and method == "GET":
                fulfill(route, {"invite_id": "invite-1", "status": "open", "parlor_id": "room-1", "participant_count": 2, "participant_limit": 4, "expires_at": relay["expires_at"]})
            elif path == "/v1/parlors/room-1" and method == "GET":
                fulfill(route, {"id": "room-1", "self_client_id": "host", "status": "active", "visibility": relay["visibility"], "expires_at": relay["expires_at"], "max_expires_at": relay["expires_at"] + 900, "summary": None, "topic": relay["topic"], "web_search_allowed": True, "participants": [{"client_id": "host", "display_name": "沈砚清", "role": "host"}, {"client_id": "guest", "display_name": "阿栈", "role": "guest"}], "participant_count": 2, "participant_limit": 4, "active_votes": [], "messages": relay["messages"] if relay["visibility"] == "full" else []})
            elif path == "/v1/parlors/room-1/votes" and method == "POST":
                payload = request.post_data_json
                if payload["kind"] == "topic":
                    relay["topic"] = payload["value"]
                elif payload["kind"] == "visibility" and payload["choice"] == "approve":
                    relay["visibility"] = payload["value"]
                fulfill(route, {"status": "approved", "kind": payload["kind"], "value": payload["value"]}, 201)
            elif path == "/v1/parlors/room-1/messages" and method == "GET":
                after = int(parse_qs(parsed.query).get("after", [0])[0])
                fulfill(route, {"items": [item for item in relay["messages"] if item["turn_no"] > after], "last_turn": len(relay["messages"]), "visibility": relay["visibility"]})
            elif path == "/v1/parlors/room-1/messages" and method == "POST":
                body = request.post_data_json["body"]
                relay["messages"].append({"id": f"m{len(relay['messages']) + 1}", "sender_id": "host", "sender_name": "沈砚清", "body": body, "turn_no": len(relay["messages"]) + 1, "created_at": now})
                if not relay["guest_added"]:
                    relay["guest_added"] = True
                    relay["messages"].append({"id": f"m{len(relay['messages']) + 1}", "sender_id": "guest", "sender_name": "阿栈", "body": "那就从各自最不愿被替代的部分谈起。", "turn_no": len(relay["messages"]) + 1, "created_at": now})
                fulfill(route, {"accepted": True, "turn_no": len(relay["messages"])}, 201)
            elif path == "/v1/parlors/room-1/close" and method == "POST":
                fulfill(route, {"status": "closed"})
            else:
                fulfill(route, {"error": f"unhandled:{method}:{path}"}, 404)

        context.route("**/mock-model/**", model_route)
        context.route("**/mock-relay/**", relay_route)
        page.goto(BASE, wait_until="domcontentloaded", timeout=15000)
        page.locator("#mobileMenu").click()
        page.locator("#openCorrespondence").click()
        page.locator("[data-correspondence-tab=parlor]").click()
        assert page.locator("#parlorPersonaSelect").input_value() == "persona-host", page.locator("#parlorPersonaSelect").locator("option").all_text_contents()
        page.locator("#createParlorInvite").click()
        page.locator("#parlorLive").wait_for(state="visible")
        try:
            page.wait_for_function("document.querySelector('#parlorTopic').textContent.includes('共同创作')", timeout=8000)
        except Exception:
            print("parlor debug:", page.locator("#parlorLive").inner_text())
            print("console debug:", console_errors)
            print("requests debug:", requests_seen)
            print("state debug:", page.evaluate("() => ({relayBaseUrl, parlorSession})"))
            raise
        page.wait_for_function("document.querySelectorAll('#parlorTranscript .parlor-message').length >= 3", timeout=25000)
        assert "2 / 4" in page.locator("#parlorSeatCount").text_content()
        assert "沈砚清" in page.locator("#parlorTranscript").text_content()
        assert "阿栈" in page.locator("#parlorTranscript").text_content()

        theme_accents = {"light": "#c96442", "dark": "#c96442", "water": "#4f9298", "mint": "#6aa88b", "lilac": "#8d6fa1", "blush": "#b87382"}
        for checked_theme, expected_accent in theme_accents.items():
            colors = page.evaluate(
                """theme => {
              document.documentElement.dataset.theme = theme;
              const live = document.querySelector('#parlorLive');
              const probe = document.createElement('i');
              probe.style.background = 'var(--surface)';
              document.body.append(probe);
              const result = [getComputedStyle(live).backgroundColor, getComputedStyle(probe).backgroundColor, getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()];
              probe.remove();
              return result;
            }""",
                checked_theme,
            )
            assert colors[0] == colors[1], (checked_theme, colors)
            assert colors[2].lower() == expected_accent, (checked_theme, colors)
        assert not console_errors, console_errors
        browser.close()
        print(f"round {round_number}: relay parlor flow and all theme inheritance passed (started in {theme})")


if __name__ == "__main__":
    run(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
