"""
keep_alive.py
==============
Keeps the deployed Streamlit Community Cloud app awake.

WHY THIS EXISTS: Streamlit Community Cloud puts apps to sleep after a
period of no real visits. A plain HTTP ping (e.g. UptimeRobot's default
monitor) does NOT prevent this — it only fetches a small static HTML
shell; the actual Python app (and its background schedulers) only start
once a real browser executes the page's JavaScript and opens a WebSocket
connection. This script uses Playwright (a real headless Chromium
browser) to do exactly that: load the page like a person would, and if
Streamlit shows its "Zzz... this app has gone to sleep" screen, click
the "Yes, get this app back up!" button.

Run on a schedule (see .github/workflows/keep-alive.yml) — every 10
minutes is comfortably inside Streamlit's inactivity window, so the app
should never be asleep for a real visitor.
"""

import sys
import time

from playwright.sync_api import sync_playwright

APP_URL = "https://doclylive.streamlit.app"
WAKE_BUTTON_TEXT = "Yes, get this app back up!"
PAGE_LOAD_TIMEOUT_MS = 30_000
POST_CLICK_WAIT_SECONDS = 15


def ping_app() -> bool:
    """Returns True if the app was reached (asleep-and-woken, or already awake)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(APP_URL, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")

            # Give the page a moment to render Streamlit's sleep screen
            # (if any) or start booting the real app.
            page.wait_for_timeout(3000)

            wake_button = page.get_by_text(WAKE_BUTTON_TEXT, exact=False)
            if wake_button.count() > 0:
                print("App is asleep — clicking 'Yes, get this app back up!'")
                wake_button.first.click()
                # Waking up can take a while the first time (cold start +
                # any pip installs if requirements.txt changed).
                page.wait_for_timeout(POST_CLICK_WAIT_SECONDS * 1000)
                print("Wake-up clicked. App should be booting now.")
            else:
                print("App is already awake — nothing to do.")

            return True
        except Exception as exc:
            print(f"Failed to reach the app: {exc}", file=sys.stderr)
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    ok = ping_app()
    sys.exit(0 if ok else 1)
