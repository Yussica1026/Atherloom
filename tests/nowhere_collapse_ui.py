import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


target = sys.argv[1] if len(sys.argv) > 1 else Path("frontend/assets/nowhere/index.html").resolve().as_uri()

with sync_playwright() as playwright:
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    browser = playwright.chromium.launch(headless=True, executable_path=str(edge) if edge.exists() else None)
    page = browser.new_page(viewport={"width": 390, "height": 640}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(target, wait_until="networkidle")
    panel = page.locator("#now")
    toggle = page.locator("#nowtoggle")
    assert "collapsed" in (panel.get_attribute("class") or ""), {
        "class": panel.get_attribute("class"),
        "mobile": page.evaluate('matchMedia("(max-width:700px)").matches'),
        "errors": errors,
    }
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    assert "collapsed" not in (panel.get_attribute("class") or "")
    assert toggle.get_attribute("aria-expanded") == "true"
    toggle.click()
    assert "collapsed" in (panel.get_attribute("class") or "")
    assert not errors, errors
    browser.close()
