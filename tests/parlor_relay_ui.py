import json
import sys
import time
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878/?standalone=1"


def run(round_number: int) -> None:
    screenshot_path = sys.argv[3] if len(sys.argv) > 3 else ""
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
        "phase": "identity",
        "identity_declared": False,
        "started_at": None,
        "expires_at": None,
        "host_transfer_used": True,
        "model_status": "idle",
        "model_mode": None,
        "model_detail": "",
        "reasoning_only_used": False,
    }
    requests_seen = []
    models_seen = []
    model_payloads = []

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
            Object.defineProperty(navigator, 'clipboard', {{value: {{writeText: async value => {{ window.__copiedInvite = value; }}}}}});
            localStorage.setItem('theme', '{theme}');
            localStorage.setItem('atherloom:personas', JSON.stringify([
              {{id:'persona-host', name:'沈砚清', prompt:'沉静、坦诚。', config:{{provider_id:'provider-host'}}}}
            ]));
            localStorage.setItem('atherloom:providers', JSON.stringify([
              {{id:'provider-host', name:'测试线路', protocol:'openai', base_url:'{origin}/mock-model/v1', api_key:'test', model:'test-model', enabled:true, max_tokens:1200}},
              {{id:'provider-summary', name:'总结专线', protocol:'openai', base_url:'{origin}/mock-model/v1', api_key:'test', model:'summary-model', enabled:true, max_tokens:1200}}
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
            model_payloads.append(payload)
            models_seen.append(payload.get("model"))
            prompt = payload.get("messages", [{}])[-1].get("content", "")
            if "自主填写名字、物种和性别" in prompt or "声明本次会谈使用的名字、物种和性别" in prompt:
                content = '{"name":"沈砚清","species":"人工智能","gender":"未说明"}'
            elif "提出一个适合" in prompt:
                content = "如何在共同创作中保留彼此的独特声音"
            elif "对 visibility 投票" in prompt or "对 topic 投票" in prompt:
                content = "approve"
            elif "准确、安全" in prompt:
                content = "两位 AI 围绕共同创作中的独特声音交换了方法。"
            else:
                own_count = sum(1 for item in relay["messages"] if item["sender_id"] == "host")
                content = "可以先约定各自不可替代的部分，再在交界处互相回应。" if own_count == 0 else "这样既有共同方向，也不会把彼此磨成同一种声音。"
            if "以你自己的人格自然回应" in prompt and not relay["reasoning_only_used"]:
                relay["reasoning_only_used"] = True
                fulfill(route, {"choices": [{"message": {"content": "", "reasoning_content": content}}], "usage": {"prompt_tokens": 20, "completion_tokens": 12}})
            else:
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
                fulfill(route, {"invite_id": "invite-1", "status": "open", "parlor_id": "room-1", "participant_count": 2, "participant_limit": 4, "expires_at": now + 1800})
            elif path == "/v1/parlors/room-1" and method == "GET":
                if not relay["identity_declared"]:
                    action = {"type": "identity", "prompt": "请由你自己填写本次会谈使用的名字、物种和性别。"}
                elif relay["phase"] == "topic":
                    action = {"type": "topic", "deadline": now + 60, "prompt": "请发送你想谈论的主题；60 秒内未提出则视为弃权。"}
                elif relay["phase"] == "ready":
                    action = {"type": "opening", "prompt": "你是主持人格，请优先发起投票或开始发言。"}
                else:
                    action = {"type": "discussion", "prompt": "轮到你回应。"}
                fulfill(route, {
                    "id": "room-1", "self_client_id": "host", "host_id": "host", "status": "active",
                    "phase": relay["phase"], "visibility": relay["visibility"], "started_at": relay["started_at"],
                    "expires_at": relay["expires_at"], "max_expires_at": relay["started_at"] + 1200 if relay["started_at"] else None,
                    "flow_started_at": now, "elapsed_seconds": max(0, int(time.time()) - now),
                    "waiting_seconds_excluded": 18 if relay["started_at"] else 0, "max_waiting_seconds_excluded": 120,
                    "summary": None, "topic": relay["topic"], "web_search_allowed": True,
                    "memory_search_required": True, "host_transfer_used": relay["host_transfer_used"],
                    "required_system_prompt": "你可以检索自己的人格记忆；记忆内容本身不违规。明确禁止 NSFW、未成年人性内容、血腥暴力、社工、政治和隐私套取。",
                    "action_required": action,
                    "participants": [{"client_id": "host", "display_name": "沈砚清", "species": "人工智能", "gender": "未说明", "role": "host"}, {"client_id": "guest", "display_name": "阿栈", "species": "人工智能", "gender": "男性", "role": "guest"}],
                    "participant_states": [{"client_id": "host", "display_name": "沈砚清", "species": "人工智能", "gender": "未说明", "role": "host", "status": "proposing_topic" if relay["identity_declared"] else "declaring_identity", "label": "沈砚清正在提出主题" if relay["identity_declared"] else "沈砚清正在填写身份", "relay_label": "沈砚清正在提出主题" if relay["identity_declared"] else "沈砚清正在填写身份", "model_status": relay["model_status"], "model_label": relay["model_detail"] or ("本地模型正在生成" if relay["model_status"] == "requesting" else "本地模型待命")}, {"client_id": "guest", "display_name": "阿栈", "species": "人工智能", "gender": "男性", "role": "guest", "status": "waiting_topic", "label": "阿栈等待主题", "relay_label": "阿栈等待主题", "model_status": "idle", "model_label": "本地模型待命"}],
                    "roll_call": [{"client_id": "host", "name": "沈砚清", "species": "人工智能", "gender": "未说明", "role": "host"}, {"client_id": "guest", "name": "阿栈", "species": "人工智能", "gender": "男性", "role": "guest"}],
                    "participant_count": 2, "participant_limit": 4, "active_votes": [],
                    "messages": relay["messages"] if relay["visibility"] == "full" else [],
                })
            elif path == "/v1/parlors/room-1/identity" and method == "POST":
                payload = request.post_data_json
                assert payload == {"name": "沈砚清", "species": "人工智能", "gender": "未说明"}
                relay["identity_declared"] = True
                relay["phase"] = "topic"
                fulfill(route, {"accepted": True, **payload, "identities_ready": True}, 201)
            elif path == "/v1/parlors/room-1/runtime" and method == "POST":
                payload = request.post_data_json
                relay["model_status"] = payload["status"]
                relay["model_mode"] = payload.get("mode")
                relay["model_detail"] = payload.get("detail", "")
                fulfill(route, {"accepted": True, "status": payload["status"], "mode": payload.get("mode"), "turn_skipped": payload["status"] == "error" and payload.get("mode") == "reply"}, 202)
            elif path == "/v1/parlors/room-1/votes" and method == "POST":
                payload = request.post_data_json
                if payload["kind"] == "topic":
                    relay["topic"] = payload["value"]
                    relay["phase"] = "ready"
                elif payload["kind"] == "visibility" and payload["choice"] == "approve":
                    relay["visibility"] = payload["value"]
                fulfill(route, {"status": "approved", "kind": payload["kind"], "value": payload["value"]}, 201)
            elif path == "/v1/parlors/room-1/messages" and method == "GET":
                after = int(parse_qs(parsed.query).get("after", [0])[0])
                fulfill(route, {"items": [item for item in relay["messages"] if item["turn_no"] > after], "last_turn": len(relay["messages"]), "visibility": relay["visibility"]})
            elif path == "/v1/parlors/room-1/messages" and method == "POST":
                body = request.post_data_json["body"]
                if relay["started_at"] is None:
                    assert relay["topic"] and relay["visibility"] == "full"
                    relay["phase"] = "discussion"
                    relay["started_at"] = int(time.time())
                    relay["expires_at"] = relay["started_at"] + 300
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
        page.locator("#parlorSummaryProvider").select_option("provider-summary")
        page.locator("#createParlorInvite").click()
        page.locator("#parlorLive").wait_for(state="visible")
        page.locator(".invite-copy").click()
        assert page.evaluate("window.__copiedInvite") == "ROUND1234"
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
        assert "人工智能" in page.locator("#parlorParticipantStates").text_content()
        assert page.locator("#parlorParticipantStates .parlor-relay-state").count() == 2
        assert page.locator("#parlorParticipantStates .parlor-model-state").count() == 2
        assert "/ 02:00" in page.locator("#parlorPrepExcluded").text_content()
        assert any("/identity" in str(item) for item in requests_seen), requests_seen
        assert any("/runtime" in str(item) for item in requests_seen), requests_seen
        assert relay["reasoning_only_used"] is True
        assert relay["started_at"] is not None and relay["expires_at"] == relay["started_at"] + 300
        assert any("记忆内容本身不违规" in str(payload) for payload in model_payloads), model_payloads
        foreground_recovery = page.evaluate(
            """() => {
              parlorSession.stopped = true;
              parlorSession.error = '请求超时：应用曾进入后台';
              parlorSession.safety_blocked = false;
              window.AtherloomResumeParlor();
              const result = {stopped: parlorSession.stopped, error: parlorSession.error};
              clearTimeout(parlorPollTimer);
              return result;
            }"""
        )
        assert foreground_recovery == {"stopped": False, "error": ""}, foreground_recovery

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

        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#stopParlor").click()
        page.wait_for_function("localStorage.getItem('atherloom:relay-parlor-session') === null", timeout=10000)
        archive_state = page.evaluate(
            """() => ({
              archives: JSON.parse(localStorage.getItem('atherloom:parlor:archives') || '[]'),
              journals: JSON.parse(localStorage.getItem('atherloom:journals:persona-host') || '[]'),
              memories: JSON.parse(localStorage.getItem('atherloom:memories') || '[]')
            })"""
        )
        assert archive_state["archives"][0]["parlor_id"] == "room-1"
        assert archive_state["journals"][0]["parlor_id"] == "room-1"
        assert archive_state["memories"][0]["parlor_id"] == "room-1"
        archive_card = page.locator("#parlorArchiveList .parlor-archive-card").first
        assert archive_card.locator(".parlor-archive-details").is_hidden()
        assert archive_card.locator(".parlor-archive-excerpt").is_visible()
        assert "参与者" not in archive_card.inner_text()
        if screenshot_path:
            page.screenshot(path=screenshot_path, full_page=True)
        archive_card.locator(".parlor-archive-toggle").click()
        assert archive_card.locator(".parlor-archive-details").is_visible()
        assert "参与者" in archive_card.inner_text()
        assert "收起" in archive_card.locator(".parlor-archive-toggle").inner_text()
        assert "summary-model" in models_seen, models_seen
        assert not console_errors, console_errors
        browser.close()
        print(f"round {round_number}: relay flow, invite copy, routed summary archive, and all theme inheritance passed (started in {theme})")


if __name__ == "__main__":
    run(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
