import sys

from playwright.sync_api import sync_playwright


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878/?standalone=1"


def run(round_number: int) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        console_errors = []
        dialogs = []

        def dismiss_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        def watch(current_page):
            current_page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error" and "404" not in message.text
                else None,
            )
            current_page.on("dialog", dismiss_dialog)

        watch(page)
        context.add_init_script(
            """
            localStorage.setItem('atherloom:personas', JSON.stringify([
              {id:'persona-host-a', name:'沈砚清', prompt:'', config:{pinned:true}},
              {id:'persona-host-b', name:'长余', prompt:'', config:{}}
            ]));
            localStorage.setItem('atherloom:providers', JSON.stringify([]));
            localStorage.setItem('atherloom:conversations', JSON.stringify([]));
            localStorage.setItem('atherloom:last-persona', 'persona-host-a');
            """
        )
        page.goto(BASE, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle")
        page.locator("#mobileMenu").click()
        correspondence_button = page.locator("#openCorrespondence")
        reference_button = page.locator("#openFavorites")
        assert correspondence_button.locator("small").count() == 0
        assert correspondence_button.evaluate("element => getComputedStyle(element).fontSize") == reference_button.evaluate("element => getComputedStyle(element).fontSize")
        assert correspondence_button.bounding_box()["height"] == reference_button.bounding_box()["height"]
        page.locator("#openCorrespondence").click()
        page.locator("#correspondenceSpace").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#correspondenceContacts').textContent.includes('还没有联系人申请')")

        page.locator("#newMailContact").click()
        contact_form = page.locator("#contactRequestForm")
        contact_form.locator("[name=display_name]").fill("远舟")
        contact_form.locator("[name=platform]").fill("AstrBot")
        contact_form.locator("[name=stable_id]").fill("astrbot:peer:001")
        contact_form.locator("button.primary").click()
        page.wait_for_function("document.querySelector('#correspondenceContacts').textContent.includes('等待用户批准')")
        page.locator("[data-contact-approve]").click()
        page.wait_for_function("document.querySelector('#correspondenceContacts').textContent.includes('白名单')")

        composer = page.locator("#mailComposer")
        composer.locator("[name=subject]").fill("第一封信")
        composer.locator("[name=content]").fill("愿你今天顺利。")
        composer.locator("button[type=submit]").click()
        page.wait_for_function("document.querySelector('#mailList').textContent.includes('愿你今天顺利。')")

        page.close()
        page = context.new_page()
        watch(page)
        page.goto(f"{BASE}&persistence_round={round_number}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.locator("#mobileMenu").click()
        page.locator("#openCorrespondence").click()
        page.wait_for_function("document.querySelector('#mailList').textContent.includes('愿你今天顺利。')")
        page.locator("[data-correspondence-tab=parlor]").click()

        select = page.locator("#parlorPersonaSelect")
        assert select.locator("option").all_text_contents() == ["沈砚清", "长余"]
        assert select.input_value() == "persona-host-a"
        assert select.bounding_box()["width"] >= 170

        select.select_option("persona-host-b")
        page.wait_for_function("state.persona === 'persona-host-b'")
        assert select.input_value() == "persona-host-b"
        assert not any("501" in message or "Standalone 功能仍在接入" in message for message in dialogs), dialogs
        assert not console_errors, console_errors
        browser.close()
        print(f"round {round_number}: Android parlor persona selection passed")


if __name__ == "__main__":
    run(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
