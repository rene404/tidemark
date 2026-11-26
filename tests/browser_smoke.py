"""Optional end-to-end UI test against the explicit demo, not a real macOS session.

Run: python3 tests/browser_smoke.py [--screenshot /tmp/macscope-review.png]
Requires playwright and a Chromium browser. MACSCOPE_CHROMIUM may select a binary.
"""
import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from macscope.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot")
    args = parser.parse_args()
    with socket.socket() as finder:
        finder.bind(("127.0.0.1", 0))
        port = finder.getsockname()[1]
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory) / "state"
        process = subprocess.Popen(
            [sys.executable, "-m", "macscope", "--demo", "--data-dir", str(state), "--port", str(port)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(process.stderr.read().decode())
                try:
                    with urllib.request.urlopen(url, timeout=0.25):
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.05)
            else:
                raise RuntimeError("Demo-Server nicht erreichbar")
            token = (state / "access-token").read_text().strip()
            # Populate multiple pages through the same real SQLite store.
            store = Store(state)
            for index in range(125):
                store.event("app.activated", {"name":f"Testapp {index:03d}", "bundle_id":"local.test", "pid":5500})
            payload = '<img src=x onerror="window.injected=true">'
            store.event("app.activated", {"name":payload, "bundle_id":"local.test", "pid":5501})
            with sync_playwright() as engine:
                binary = os.environ.get("MACSCOPE_CHROMIUM")
                options = {"headless":True, "args":["--no-sandbox"]}
                if binary:
                    options["executable_path"] = binary
                browser = engine.chromium.launch(**options)
                page = browser.new_page(viewport={"width":1440,"height":1100})
                page_errors = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(url)
                page.locator("#token").fill("incorrect")
                page.get_by_role("button", name="Verbinden", exact=False).click()
                page.get_by_text("Bitte den Zugriffsschlüssel eingeben.", exact=True).wait_for()
                assert page.locator("#login").is_visible()
                page.locator("#token").fill(token)
                page.locator("#connect").click()
                page.locator("#dashboard").wait_for(state="visible")
                page.locator("#apps .app").first.wait_for()
                assert page.locator("#demo").is_visible()
                assert not page.locator("#token").input_value()
                assert page.evaluate("localStorage.length") == 0
                assert page.evaluate("sessionStorage.length") == 0
                assert page.locator("#events").inner_text().find(payload) >= 0
                assert page.evaluate("window.injected === undefined")
                page.locator("#older").click()
                page.wait_for_function("document.querySelectorAll('#events .event').length > 100")
                assert "Historische Ansicht" in page.locator("#resultInfo").inner_text()
                page.locator("#refresh").click()
                page.wait_for_function("document.querySelector('#resultInfo').textContent.includes('Neueste')")
                page.locator("#search").fill("Testapp 042")
                page.wait_for_function("document.querySelectorAll('#events .event').length === 1")
                assert "Testapp 042" in page.locator("#events").inner_text()
                page.locator("#search").fill("")
                page.wait_for_function("document.querySelectorAll('#events .event').length > 1")
                if args.screenshot:
                    page.screenshot(path=args.screenshot, full_page=False)
                firefox = page.locator("#apps .app").filter(has_text="org.mozilla.firefox")
                firefox.get_by_role("button", name="Beenden", exact=True).click()
                page.locator("#quitDialog").wait_for(state="visible")
                page.locator("#cancelQuit").click()
                assert firefox.is_visible()
                firefox.get_by_role("button", name="Beenden", exact=True).click()
                page.locator("#confirmQuit").click()
                page.get_by_text("App-Ende bestätigt", exact=True).wait_for(timeout=15000)
                assert page.locator("#apps .app").filter(has_text="org.mozilla.firefox").count() == 0
                page.set_viewport_size({"width":390,"height":844})
                page.wait_for_timeout(100)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.locator("#disconnect").click()
                assert page.locator("#login").is_visible()
                assert page.locator("#events .event").count() == 0
                assert not page_errors, page_errors
                browser.close()
            print("Browserprüfung bestanden: Anmeldung, Datenanzeige, Suche, Pagination, XSS-Abwehr, Beenden-Dialog, bestätigtes simuliertes App-Ende, Mobilansicht, Trennen.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stderr.close()


if __name__ == "__main__":
    main()
