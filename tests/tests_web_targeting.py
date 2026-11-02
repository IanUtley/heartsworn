"""The browser's targeting cursor: a targeting cross while a target is chosen, the normal pointer once none is left.

Drives the real page in headless Chromium against a live ``web_client`` server. Skipped when Playwright or the Chromium
binary is not available (set ``HEX_CHROMIUM`` to point at another one).
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CHROMIUM = os.environ.get("HEX_CHROMIUM", "/snap/bin/chromium")

sync_api = pytest.importorskip("playwright.sync_api")
if not os.path.exists(CHROMIUM):
    pytest.skip(f"no Chromium at {CHROMIUM}", allow_module_level=True)


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "web_client.server", "--port", str(port)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    try:
        deadline = time.time() + 90
        while True:
            try:
                with urllib.request.urlopen(url + "api/decks", timeout=5) as reply:
                    if reply.status == 200:
                        break
            except OSError:
                pass
            if time.time() > deadline or proc.poll() is not None:
                pytest.skip("the web client server did not start")
            time.sleep(0.5)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=15)


@pytest.fixture
def page(server_url):
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"], headless=True)
        try:
            tab = browser.new_page(viewport={"width": 1400, "height": 900})
            tab.goto(server_url)
            tab.wait_for_function("document.querySelector('#deck-mine') && "
                                  "document.querySelector('#deck-mine').options.length > 0", timeout=30000)
            yield tab
        finally:
            browser.close()


def _cursor(tab):
    return tab.evaluate("getComputedStyle(document.body).cursor")


def test_targeting_cursor_is_a_cross_while_a_target_is_chosen_and_reverts_after(page):
    # Start a match through the lobby (the folder gate is a UI check; the battle does not need the art).
    page.click("#nav-battle")   # the main page's Battle button opens the lobby
    page.wait_for_selector("#lobby:not(.hidden)")
    page.evaluate("document.querySelector('#start-match').disabled = false")
    page.click("#start-match")
    page.wait_for_function("typeof S !== 'undefined' && S.matchId && S.prompt", timeout=90000)
    # Answer the opening prompts the way a player does, until a priority window is open with nothing in flight.
    opening = {"goes_first": "play_first", "mulligan": "keep"}
    deadline = time.time() + 90
    while page.evaluate("S.prompt && S.prompt.kind") != "priority" or page.evaluate("S.busy"):
        if time.time() > deadline:
            pytest.fail("the match did not reach a priority window")
        kind = page.evaluate("S.prompt && S.prompt.kind")
        if kind in opening and not page.evaluate("S.busy"):
            page.evaluate(f"document.querySelector('[data-act={opening[kind]}]').click()")
        time.sleep(0.3)
    assert "target_cursor.svg" not in _cursor(page)

    # The target mode a card's option opens: one target step. This is the state startAction() sets.
    page.evaluate("""() => {
      S.ui = { mode: "target", action: { type: "play", card: 0 }, step: 0, picked: {},
               steps: [{ key: "t0", kind: "targets", index: 0, candidates: new Set([0]), min: 1, max: 1,
                         label: "Choose a target" }] };
      render();
    }""")
    assert page.evaluate("document.body.classList.contains('targeting')")
    assert "target_cursor.svg" in _cursor(page)

    # Cancel ends the target mode; the pointer goes back to normal.
    page.click("button[data-act=cancel]")
    assert not page.evaluate("document.body.classList.contains('targeting')")
    assert "target_cursor.svg" not in _cursor(page)
