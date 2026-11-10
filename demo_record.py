"""Record the SliceHouse Pizza demo: scripted WhatsApp-style conversation.

Outputs in demo/: s1-fresh.png, s2-menu.png, s3-ask-details.png,
s4-order-confirmed.png, s5-status.png, s6-preorder.png, transcript.json.
Run with the dental venv (it has Playwright + Chromium):
  ~/workspace/voice-agent-dental/.venv/bin/python demo_record.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://localhost:8502/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo")
os.makedirs(OUT, exist_ok=True)
BOX = "Type your message to SliceHouse Pizza…"


def shot(page, name):
    page.screenshot(path=os.path.join(OUT, name))
    print("saved", os.path.join(OUT, name))


def send(page, text):
    """Type a message and wait for the assistant's next reply."""
    before = page.locator('[data-testid="stChatMessage"]').count()
    box = page.get_by_placeholder(BOX)
    box.click()
    box.fill(text)
    box.press("Enter")
    page.wait_for_function(
        "(prev) => document.querySelectorAll('[data-testid=\"stChatMessage\"]').length > prev",
        arg=before,
        timeout=90000,
    )
    page.wait_for_timeout(4000)  # let TTS + rerun settle
    print("assistant replied to:", text[:60])


def ensure_ordered(page, attempts=3):
    """Keep confirming until the persistent order card appears."""
    for i in range(attempts):
        if page.get_by_text("Order confirmed:").count() > 0:
            print("order confirmed")
            return True
        print(f"order not yet placed (attempt {i + 1}) — nudging")
        send(page, "Yes, please confirm the order.")
    print("WARNING: order card did not appear after nudges")
    return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(BASE, wait_until="networkidle")
        page.get_by_text("SliceHouse Pizza").first.wait_for(timeout=30000)
        page.wait_for_timeout(2000)
        shot(page, "s1-fresh.png")

        send(page, "Hi, what's on your menu?")
        page.locator('[data-testid="stChatMessage"]').last.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        shot(page, "s2-menu.png")

        send(page, "I'd like a large pepperoni pizza and a garlic bread, delivery please.")
        page.locator('[data-testid="stChatMessage"]').last.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        shot(page, "s3-ask-details.png")

        send(page, "My name is Ali Raza, my phone is 555-019-3344, my address is 12 Main Street. Yes, please confirm the order.")
        ensure_ordered(page)
        page.get_by_text("Order confirmed:").first.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        shot(page, "s4-order-confirmed.png")

        send(page, "Where is my order?")
        page.locator('[data-testid="stChatMessage"]').last.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        shot(page, "s5-status.png")

        page.get_by_role("button", name="🌙 Simulate closed hours").click()
        page.wait_for_timeout(2000)
        send(page, "Hi, can I order a medium margherita pizza for pickup?")
        page.locator('[data-testid="stChatMessage"]').last.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        shot(page, "s6-preorder.png")

        msgs = page.locator('[data-testid="stChatMessage"]')
        texts = [msgs.nth(i).inner_text().strip() for i in range(msgs.count())]
        with open(os.path.join(OUT, "transcript.json"), "w") as f:
            json.dump({"messages": texts}, f, indent=2)
        print("TRANSCRIPT:")
        for t in texts:
            print("-", t[:160].replace("\n", " / "))
        browser.close()
        print("DONE")


if __name__ == "__main__":
    main()
