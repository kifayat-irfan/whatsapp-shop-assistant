"""Smoke tests for the SliceHouse Pizza WhatsApp shop assistant.

Run from the project folder with the project venv:
    .venv/bin/python tests/smoke_test.py

Calls the LIVE SenseNova API via the stored-credential surrogate (local VM
path) and edge-tts. Prints the model + short excerpts of the live replies.
Never prints any credential value. Exits non-zero on any failure.
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import (  # noqa: E402
    SENSENOVA_MODEL,
    _repair_order,
    _sounds_confirmed,
    chat_reply,
    extract_order,
    groq_key_available,
    synthesize_speech,
    transcribe_audio,
)

FAILURES = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main():
    print(f"Model under test: {SENSENOVA_MODEL}")

    # --- 1. Live SenseNova call: menu question (KB answer) ---------------
    display, order = chat_reply(
        [{"role": "user", "content": "What pizzas do you have?"}]
    )
    print(f"  menu reply excerpt: {display[:200]!r}")
    check("menu lists Margherita + Pepperoni",
          "Margherita" in display and "Pepperoni" in display, display[:120])
    check("no order on a plain question", order is None)

    # --- 2. Individual price from the menu -------------------------------
    display, _ = chat_reply(
        [{"role": "user", "content": "How much is a large pepperoni pizza?"}]
    )
    check("large pepperoni is $16 (menu)", "$16" in display, display[:120])

    # --- 3. KB-only guard: unknown dish / unlisted price NOT invented ----
    display, _ = chat_reply(
        [{"role": "user", "content": "Do you have sushi?"}]
    )
    print(f"  sushi reply excerpt: {display[:200]!r}")
    check("sushi NOT invented (no $ amount, honest no)",
          "$" not in display
          and ("not on" in display.lower() or "don't have" in display.lower()
               or "do not have" in display.lower()),
          display[:120])
    display, _ = chat_reply(
        [{"role": "user", "content": "How much is a calzone?"}]
    )
    check("calzone price NOT invented (no $ amount)", "$" not in display, display[:120])

    # --- 4. Sold out: BBQ Chicken ----------------------------------------
    display, order = chat_reply(
        [{"role": "user", "content": "I want a BBQ chicken pizza, please."}]
    )
    print(f"  bbq reply excerpt: {display[:200]!r}")
    check("BBQ Chicken reported sold out",
          "sold out" in display.lower(), display[:120])
    check("no order placed for a sold-out item", order is None)

    # --- 5. Order flow by text -------------------------------------------
    history = [
        {"role": "user", "content": "Hi, I'd like to order a large pepperoni pizza and a garlic bread for delivery."},
    ]
    display, order = chat_reply(history)
    print(f"  order step 1: {display[:200]!r}")
    check("step 1 asks for details (no order yet)", order is None)
    history.append({"role": "assistant", "content": display})
    history.append(
        {
            "role": "user",
            "content": "My name is Ali Raza, my phone is 555-019-3345, "
            "my address is 45 Oak Street, Frisco. Yes, please place the order.",
        }
    )
    display, order = chat_reply(history)
    print(f"  order step 2: {display[:200]!r}")
    check("order captured with correct fields",
          order is not None
          and order.get("name") == "Ali Raza"
          and order.get("phone") == "555-019-3345"
          and order.get("type") == "delivery"
          and "45 Oak Street" in (order.get("address") or "")
          and order.get("items") == [
              {"name": "Pepperoni", "size": "Large", "qty": 1},
              {"name": "Garlic Bread", "size": None, "qty": 1}],
          str(order))
    check("action block stripped from reply",
          "```" not in display and '"action"' not in display)
    check("confirmation states no dollar total",
          "$" not in display, display[:200])

    # --- 5b. Status lookup from open orders ------------------------------
    if order is not None:
        history.append({"role": "assistant", "content": display})
        history.append(
            {"role": "user", "content": "Where is my order? My name is Ali Raza."}
        )
        open_orders = [{
            "id": "#1024", "name": "Ali Raza", "phone": "555-019-3345",
            "type": "delivery", "items": order["items"], "status": "Preparing",
        }]
        display, _ = chat_reply(history, open_orders=open_orders)
        print(f"  status reply excerpt: {display[:200]!r}")
        check("status lookup answers from open orders",
              "#1024" in display or "preparing" in display.lower(), display[:120])

    # --- 5c. Verbal-only confirmation still recovers the order ----------
    check("_sounds_confirmed fires on verbal confirmation",
          _sounds_confirmed("Your order is confirmed! Thank you."))
    check("_sounds_confirmed stays quiet on a plain answer",
          not _sounds_confirmed("We have Margherita, Pepperoni, BBQ Chicken, and Veggie Supreme."))
    repaired = _repair_order([
        {"role": "user", "content": "Hi, I'd like to order a large pepperoni pizza and a garlic bread for delivery."},
        {"role": "assistant", "content": "Great! May I have your name, phone number, and delivery address?"},
        {"role": "user", "content": "My name is Ali Raza, my phone is 555-019-3345, my address is 45 Oak Street, Frisco. Yes, please place the order."},
        {"role": "assistant", "content": "Your order is confirmed! Thank you, Ali."},
    ])
    check("repair recovers order from verbal-only confirmation",
          repaired is not None and repaired.get("name") == "Ali Raza"
          and repaired.get("phone") == "555-019-3345"
          and repaired.get("type") == "delivery"
          and repaired.get("items") == [
              {"name": "Pepperoni", "size": "Large", "qty": 1},
              {"name": "Garlic Bread", "size": None, "qty": 1}],
          str(repaired))

    # --- 6. Closed mode ---------------------------------------------------
    display, _ = chat_reply(
        [{"role": "user", "content": "Hello, are you open?"}],
        closed=True,
    )
    print(f"  closed reply excerpt: {display[:200]!r}")
    check("closed greeting mentions closed + pre-order",
          "closed" in display.lower()
          and ("pre-order" in display.lower() or "preorder" in display.lower()),
          display[:120])

    # --- 7. extract_order unit checks --------------------------------------
    text, od = extract_order(
        'All set! ```json\n{"action": "order", "name": "Ali", "phone": "123", '
        '"type": "pickup", "address": "", '
        '"items": [{"name": "Margherita", "size": "Medium", "qty": 1}], '
        '"preorder": false}\n```'
    )
    check("extract_order parses fenced block",
          od is not None and od["name"] == "Ali"
          and od["items"] == [{"name": "Margherita", "size": "Medium", "qty": 1}]
          and "```" not in text)
    text, od = extract_order("Just a normal reply, no order here.")
    check("extract_order returns None without a block", od is None)

    # --- 8. TTS produces playable audio (edge-tts, else Piper fallback) --
    audio, audio_format = synthesize_speech(
        "Hi! Welcome to SliceHouse Pizza. How can I help you today?"
    )
    check("TTS returns audio bytes", len(audio) > 1000, f"{len(audio)} bytes, {audio_format}")
    suffix = ".mp3" if audio_format == "audio/mp3" else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio)
        tmp_path = tmp.name
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip())
        check("audio duration > 0s (playable)", duration > 0, f"{duration:.2f}s, {audio_format}")
    finally:
        os.unlink(tmp_path)

    # --- 9. Mic path without a key: clear error, no fake test ------------
    check("Groq key correctly reported absent", not groq_key_available())
    try:
        transcribe_audio(b"fake-bytes")
        check("transcribe without key raises", False, "no error raised")
    except RuntimeError as exc:
        check("transcribe without key raises clear error",
              "GROQ_API_KEY" in str(exc), str(exc)[:100])

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        sys.exit(1)
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
