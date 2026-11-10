"""Deterministic unit tests for the SliceHouse Pizza shop assistant.

Run from the project folder with the project venv:
    .venv/bin/python tests/app_test.py

No network calls: order math, item normalisation, dedupe, order-ID
sequencing, action-block parsing, and the deterministic repair extractor.
Exits non-zero on any failure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import (  # noqa: E402
    _repair_order,
    _sounds_confirmed,
    extract_order,
)
from knowledge_base import (  # noqa: E402
    canonical_item_name,
    compute_totals,
    next_order_id,
    normalize_items,
    same_order,
)

FAILURES = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main():
    # --- Order math -----------------------------------------------------
    totals = compute_totals(
        [{"name": "Pepperoni", "size": "Large", "qty": 1},
         {"name": "Garlic Bread", "size": None, "qty": 1}],
        "delivery",
    )
    check("subtotal $16 + $4.99 = $20.99",
          totals["subtotal"] == 20.99, str(totals))
    check("delivery fee $2.99 under $30",
          totals["delivery_fee"] == 2.99, str(totals))
    check("total $23.98", totals["total"] == 23.98, str(totals))

    totals = compute_totals(
        [{"name": "Pepperoni", "size": "Large", "qty": 2}], "delivery"
    )
    check("subtotal $32 over $30 -> free delivery",
          totals["subtotal"] == 32.00 and totals["delivery_fee"] == 0.0
          and totals["total"] == 32.00, str(totals))

    totals = compute_totals(
        [{"name": "Margherita", "size": "Large", "qty": 2}], "delivery"
    )
    check("subtotal exactly $30 -> fee applies (free is OVER $30)",
          totals["subtotal"] == 30.00 and totals["delivery_fee"] == 2.99,
          str(totals))

    totals = compute_totals(
        [{"name": "Pepperoni", "size": "Medium", "qty": 1}], "pickup"
    )
    check("pickup has no delivery fee",
          totals["delivery_fee"] == 0.0 and totals["total"] == 13.00, str(totals))

    # --- Item normalisation ----------------------------------------------
    valid, unknown = normalize_items([
        {"name": "pepperoni pizza", "size": "Large", "qty": 2},
        {"name": "Garlic Bread", "size": None, "qty": 1},
        {"name": "Sushi Platter", "size": None, "qty": 1},
    ])
    check("known items normalised (alias + size kept)",
          valid == [{"name": "Pepperoni", "size": "Large", "qty": 2},
                    {"name": "Garlic Bread", "size": None, "qty": 1}],
          str(valid))
    check("unknown dish reported, never priced", unknown == ["Sushi Platter"], str(unknown))

    valid, _ = normalize_items([{"name": "Margherita", "size": None, "qty": 1}])
    check("pizza without size defaults to Medium",
          valid == [{"name": "Margherita", "size": "Medium", "qty": 1}], str(valid))

    check("canonical name resolves aliases",
          canonical_item_name("chicken wings") == "Chicken Wings (6 pc)"
          and canonical_item_name("BBQ Chicken Pizza") == "BBQ Chicken"
          and canonical_item_name("sushi") is None)

    # --- Dedupe ------------------------------------------------------------
    order_a = {"name": "Ali Raza", "phone": "555-019-3345",
               "items": [{"name": "Pepperoni", "size": "Large", "qty": 1}],
               "address": "45 Oak Street"}
    order_b = {"name": "ali raza", "phone": "555-019-3345",
               "items": [{"name": "Pepperoni", "size": "Large", "qty": 1}],
               "address": "45 Oak Street "}
    order_c = {"name": "Ali Raza", "phone": "555-019-3345",
               "items": [{"name": "Pepperoni", "size": "Large", "qty": 2}],
               "address": "45 Oak Street"}
    check("identical order deduped (case/whitespace tolerant)",
          same_order(order_a, order_b))
    check("different quantity is a different order",
          not same_order(order_a, order_c))

    # --- Order IDs ----------------------------------------------------------
    check("first order ID is 1024", next_order_id([]) == 1024)
    check("next ID follows max", next_order_id([1024, 1025]) == 1026)

    # --- extract_order unit checks -----------------------------------------
    text, order = extract_order(
        'All set! ```json\n{"action": "order", "name": "Ali Raza", '
        '"phone": "555-019-3345", "type": "delivery", "address": "45 Oak Street", '
        '"items": [{"name": "Pepperoni", "size": "Large", "qty": 1}], '
        '"preorder": false}\n```'
    )
    check("extract_order parses fenced block",
          order is not None and order["name"] == "Ali Raza"
          and order["items"] == [{"name": "Pepperoni", "size": "Large", "qty": 1}]
          and "```" not in text and '"action"' not in text, str(order))
    text, order = extract_order("Just a normal reply, no order here.")
    check("extract_order returns None without a block", order is None)
    # Regression: dangling `json` tag with no backticks.
    text, order = extract_order(
        'Great, your order is confirmed!\n\njson\n'
        '{"action": "order", "name": "Ali Raza", "phone": "555-019-3345", '
        '"type": "pickup", "address": "", '
        '"items": [{"name": "Margherita", "size": "Medium", "qty": 1}], '
        '"preorder": false}'
    )
    check("extract_order strips dangling json + bare block",
          order is not None and order["name"] == "Ali Raza"
          and "```" not in text and '"action"' not in text
          and "json" not in text.lower()
          and "preorder" not in text.lower() and "], " not in text,
          repr(text[:120]))
    # Regression: JSON emitted twice, fenced and bare.
    text, order = extract_order(
        'Confirmed!\n```json\n{"action": "order", "name": "Ali Raza", '
        '"phone": "555-019-3345", "type": "pickup", "address": "", '
        '"items": [{"name": "Margherita", "size": "Medium", "qty": 1}], '
        '"preorder": false}\n```\n'
        '{"action": "order", "name": "Ali Raza", "phone": "555-019-3345", '
        '"type": "pickup", "address": "", '
        '"items": [{"name": "Margherita", "size": "Medium", "qty": 1}], '
        '"preorder": false}'
    )
    check("extract_order strips double emission",
          order is not None and order["name"] == "Ali Raza"
          and "```" not in text and '"action"' not in text
          and "preorder" not in text.lower() and "], " not in text,
          repr(text[:120]))
    # Regression: truncated second emission (cut off by the token limit)
    # leaves a fragment with unbalanced braces after the good block.
    text, order = extract_order(
        'Confirmed!\n```json\n{"action": "order", "name": "Ali Raza", '
        '"phone": "555-019-3345", "type": "pickup", "address": "", '
        '"items": [{"name": "Margherita", "size": "Medium", "qty": 1}], '
        '"preorder": false}\n```\n'
        '{"action": "order", "name": "Ali Raza", "phone": "555-019-3345", '
        '"type": "pickup", "address": "", "items": [{"name": "Margherita"'
    )
    check("extract_order drops truncated second emission",
          order is not None and order["name"] == "Ali Raza"
          and "```" not in text and '"action"' not in text
          and "preorder" not in text.lower(), repr(text[:120]))
    # Unknown dish only -> no order at all.
    text, order = extract_order(
        '```json\n{"action": "order", "name": "Ali", "phone": "555-019-3345", '
        '"type": "pickup", "address": "", '
        '"items": [{"name": "Sushi Platter", "size": null, "qty": 1}], '
        '"preorder": false}\n```'
    )
    check("block with only an unknown dish yields no order", order is None)

    # --- Confirmation-sound trigger -----------------------------------------
    check("_sounds_confirmed fires on verbal confirmation",
          _sounds_confirmed("Your order is confirmed! Thank you."))
    check("_sounds_confirmed fires on placed",
          _sounds_confirmed("Your pizza order has been placed."))
    check("_sounds_confirmed stays quiet on a plain answer",
          not _sounds_confirmed("We have Margherita, Pepperoni, BBQ Chicken, and Veggie Supreme."))

    # --- Deterministic repair ------------------------------------------------
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
          and repaired.get("address") == "45 Oak Street, Frisco"
          and repaired.get("items") == [
              {"name": "Pepperoni", "size": "Large", "qty": 1},
              {"name": "Garlic Bread", "size": None, "qty": 1}],
          str(repaired))

    repaired = _repair_order([
        {"role": "user", "content": "I want 2 medium margherita pizzas for pickup."},
        {"role": "assistant", "content": "And your name and phone?"},
        {"role": "user", "content": "My name is Sara Ahmed, my phone is 555-014-2287. Yes, place it."},
        {"role": "assistant", "content": "Your order has been placed!"},
    ])
    check("repair recovers pickup order with quantities",
          repaired is not None and repaired.get("type") == "pickup"
          and repaired.get("items") == [
              {"name": "Margherita", "size": "Medium", "qty": 2}],
          str(repaired))

    repaired = _repair_order([
        {"role": "user", "content": "I'd like a large pepperoni for delivery."},
        {"role": "user", "content": "My name is Ali Raza, my phone is 555-019-3345. Yes, confirm it."},
    ])
    check("repair returns None without a delivery address",
          repaired is None, str(repaired))

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        sys.exit(1)
    print("ALL APP TESTS PASSED")


if __name__ == "__main__":
    main()
