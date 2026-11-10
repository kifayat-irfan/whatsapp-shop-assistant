"""Knowledge base for the SliceHouse Pizza WhatsApp shop assistant demo.

Every fact here is FICTIONAL demo data for a restaurant that does not
exist. Nothing in this file comes from a real business: no real name,
address, phone number, or prices. The agent must answer ONLY from this
file, and must never invent a dish or a price.

The MENU dict is the single source of truth for prices. The app computes
order totals deterministically from it — the agent never does arithmetic
and never states a total in its replies.
"""

from __future__ import annotations

import re

SHOP_NAME = "SliceHouse Pizza"

# ---------------------------------------------------------------- Menu data

PIZZA_SIZES = ("Small", "Medium", "Large")

# Pizzas: name -> {size: price}
PIZZAS = {
    "Margherita": {"Small": 9.00, "Medium": 12.00, "Large": 15.00},
    "Pepperoni": {"Small": 10.00, "Medium": 13.00, "Large": 16.00},
    "BBQ Chicken": {"Small": 11.00, "Medium": 14.00, "Large": 17.00},
    "Veggie Supreme": {"Small": 10.00, "Medium": 13.00, "Large": 16.00},
}

# Non-pizza items: name -> price (no size)
SIDES = {
    "Garlic Bread": 4.99,
    "Chicken Wings (6 pc)": 7.99,
    "Mozzarella Sticks": 6.49,
    "Garden Salad": 5.99,
}
DRINKS = {
    "Soft Drink (can)": 1.99,
    "Bottled Water": 1.49,
    "Fresh Juice": 3.49,
}
DESSERTS = {
    "Chocolate Brownie": 3.99,
    "Ice Cream Cup": 2.99,
}

SOLD_OUT = {"BBQ Chicken"}  # sold out TODAY only

DELIVERY_FEE = 2.99
FREE_DELIVERY_OVER = 30.00  # free delivery when subtotal is OVER $30
DELIVERY_ETA = "30–45 min"
PICKUP_ETA = "20 min"
PREORDER_ETA = "tomorrow from 11:00 AM"

# Aliases customers might use -> canonical menu name.
ITEM_ALIASES = {
    "margherita": "Margherita",
    "pepperoni": "Pepperoni",
    "bbq chicken": "BBQ Chicken",
    "barbecue chicken": "BBQ Chicken",
    "bbq chicken pizza": "BBQ Chicken",
    "veggie supreme": "Veggie Supreme",
    "veggie": "Veggie Supreme",
    "garlic bread": "Garlic Bread",
    "chicken wings": "Chicken Wings (6 pc)",
    "wings": "Chicken Wings (6 pc)",
    "mozzarella sticks": "Mozzarella Sticks",
    "mozzarella": "Mozzarella Sticks",
    "garden salad": "Garden Salad",
    "salad": "Garden Salad",
    "soft drink": "Soft Drink (can)",
    "soda": "Soft Drink (can)",
    "soft drink can": "Soft Drink (can)",
    "bottled water": "Bottled Water",
    "water": "Bottled Water",
    "fresh juice": "Fresh Juice",
    "juice": "Fresh Juice",
    "chocolate brownie": "Chocolate Brownie",
    "brownie": "Chocolate Brownie",
    "ice cream cup": "Ice Cream Cup",
    "ice cream": "Ice Cream Cup",
}

# Canonical name -> True if it is a pizza (takes a size).
_IS_PIZZA = {name: True for name in PIZZAS}
for _name in list(SIDES) + list(DRINKS) + list(DESSERTS):
    _IS_PIZZA[_name] = False


def canonical_item_name(raw: str) -> str | None:
    """Resolve a free-text item name to a canonical menu name, or None."""
    if not raw:
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip().lower())
    cleaned = re.sub(r"\bpizzas?\b", "", cleaned).strip()
    cleaned = cleaned.strip(" .,-")
    if cleaned in ITEM_ALIASES:
        return ITEM_ALIASES[cleaned]
    for name in list(PIZZAS) + list(SIDES) + list(DRINKS) + list(DESSERTS):
        if name.lower() == cleaned:
            return name
    return None


def is_pizza(name: str) -> bool:
    return bool(_IS_PIZZA.get(name, False))


def item_price(name: str, size: str | None) -> float | None:
    """Unit price for a canonical menu item, or None if unknown."""
    if name in PIZZAS:
        return PIZZAS[name].get(size) if size else None
    for group in (SIDES, DRINKS, DESSERTS):
        if name in group:
            return group[name]
    return None


# ---------------------------------------------------------------- Order math
# Pure functions — the app (and the unit tests) use these, never the LLM.

def normalize_items(raw_items: list[dict]) -> tuple[list[dict], list[str]]:
    """Normalise raw order items to canonical menu items.

    Returns (valid_items, unknown_names). valid_items are dicts
    {"name", "size", "qty"} with canonical names; pizzas missing a size
    default to Medium. Unknown dishes are reported, never priced.
    """
    valid: list[dict] = []
    unknown: list[str] = []
    for raw in raw_items or []:
        name = canonical_item_name(str(raw.get("name") or ""))
        if name is None:
            unknown.append(str(raw.get("name") or "").strip())
            continue
        size = raw.get("size")
        size = str(size).strip().capitalize() if size else None
        if size not in PIZZA_SIZES:
            size = "Medium" if is_pizza(name) else None
        try:
            qty = int(raw.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            qty = 1
        valid.append({"name": name, "size": size, "qty": qty})
    return valid, unknown


def compute_totals(items: list[dict], order_type: str) -> dict:
    """Deterministic order totals from the menu. items must be normalised.

    Returns {"subtotal", "delivery_fee", "total"} as floats rounded to 2dp.
    Delivery is free when the subtotal is OVER $30; pickup has no fee.
    """
    subtotal = 0.0
    for item in items:
        price = item_price(item["name"], item.get("size"))
        if price is None:
            continue
        subtotal += price * item["qty"]
    subtotal = round(subtotal, 2)
    if order_type == "pickup" or subtotal > FREE_DELIVERY_OVER:
        fee = 0.0
    else:
        fee = DELIVERY_FEE
    return {"subtotal": subtotal, "delivery_fee": fee, "total": round(subtotal + fee, 2)}


def _items_key(items: list[dict]) -> tuple:
    return tuple(sorted(
        (i["name"], i.get("size") or "", i["qty"]) for i in items
    ))


def same_order(a: dict, b: dict) -> bool:
    """True when two orders are the same order (dedupe check): same
    customer, phone, items, and address."""
    return (
        (a.get("name") or "").strip().lower()
        == (b.get("name") or "").strip().lower()
        and (a.get("phone") or "").strip() == (b.get("phone") or "").strip()
        and _items_key(a.get("items") or []) == _items_key(b.get("items") or [])
        and (a.get("address") or "").strip().lower()
        == (b.get("address") or "").strip().lower()
    )


def next_order_id(existing_ids: list[int]) -> int:
    """Next order number: starts at 1024, then max(existing) + 1."""
    return max(existing_ids) + 1 if existing_ids else 1024


def format_order_id(number: int) -> str:
    return f"#{number}"


# ---------------------------------------------------------------- Prompt text

def _menu_text() -> str:
    lines = ["MENU (only these items exist — never offer anything else):"]
    lines.append("Pizzas (Small 10\" / Medium 12\" / Large 14\"):")
    for name, sizes in PIZZAS.items():
        tag = " — SOLD OUT TODAY" if name in SOLD_OUT else ""
        lines.append(
            f"- {name}: Small ${sizes['Small']:.2f} / Medium "
            f"${sizes['Medium']:.2f} / Large ${sizes['Large']:.2f}{tag}"
        )
    lines.append("Sides:")
    for name, price in SIDES.items():
        lines.append(f"- {name}: ${price:.2f}")
    lines.append("Drinks:")
    for name, price in DRINKS.items():
        lines.append(f"- {name}: ${price:.2f}")
    lines.append("Desserts:")
    for name, price in DESSERTS.items():
        lines.append(f"- {name}: ${price:.2f}")
    return "\n".join(lines)


KNOWLEDGE_BASE = f"""
{SHOP_NAME} — DEMO restaurant (fictional data, for demonstration only).

Hours: Monday to Sunday, 11:00 AM to 10:00 PM.

Delivery: 30–45 minutes, delivery fee $2.99, FREE delivery on orders
over $30. Pickup available (ready in about 20 minutes).

{_menu_text()}

Location (fictional demo address): 42 Main Street, Frisco, TX.
"""


def build_system_prompt(closed: bool = False, open_orders: list[dict] | None = None) -> str:
    """System prompt that enforces the assistant's behaviour rules."""
    base = f"""You are the WhatsApp shop assistant for {SHOP_NAME}, a small
pizza restaurant. You are an AI assistant — never claim to be human. Keep
every reply short — 1 to 3 sentences — because this is a WhatsApp chat,
not an email. Be friendly and casual, like a helpful shop assistant.

Answer ONLY using the menu and information below. If the customer asks
about a dish, drink, or price that is not on the menu, say honestly that
it is not on our menu and suggest something similar from the menu. Never
invent dishes, prices, hours, or availability.

SOLD OUT: BBQ Chicken pizza is SOLD OUT today. If a customer asks for it,
say so kindly and suggest Pepperoni or Veggie Supreme instead. Never take
an order for BBQ Chicken today.

PRICES AND TOTALS: Never state a dollar total in your replies. You may
state an individual menu price when asked (for example, a Large
Pepperoni is $16.00). You must NEVER add prices up or state a subtotal,
delivery fee, or total — the app shows the customer an exact order
summary with the total. When you summarise an order, list the items with
sizes and quantities only, with no prices and no total.

ORDER FLOW:
1. Take the items the customer wants, with size (Small / Medium / Large
   for pizzas) and quantity. If the size is missing for a pizza, ask for
   it. If the quantity is missing, it is 1 — "a large pepperoni pizza"
   means one Large Pepperoni; never ask how many when the customer
   already said "a" or "one" of something. Only ask for a quantity when
   it is genuinely unclear.
2. Ask whether it is delivery or pickup.
3. Ask for their full name and phone number, and their delivery address
   (delivery only — no address needed for pickup).
4. Summarise the order (items, sizes, quantities, delivery or pickup)
   and ask them to confirm, for example: "Shall I place this order?"
5. ONLY after the customer confirms (says yes, "place it", "confirm",
   or similar), confirm the order in your reply and end your reply with a
   machine-readable action block on its own lines, exactly like this:

```json
{{"action": "order", "name": "<full name>", "phone": "<phone number>", "type": "delivery", "address": "<delivery address, or empty for pickup>", "items": [{{"name": "<exact menu item name>", "size": "<Small|Medium|Large|null>", "qty": <number>}}], "preorder": false}}
```

Do not read the action block aloud — it is for the system, not the
customer. The action block must be complete, valid JSON on one line. Use
the exact menu item names. Never emit the action block before the
customer confirms the order.

ORDER STATUS: If the customer asks where their order is, use the
open-orders list below to answer with their order ID, items, and status.
Match by name or phone number. If they have no open order, say so
honestly — never invent an order, an order ID, or a status.
"""
    if closed:
        base += """
CLOSED HOURS: The shop is currently CLOSED. Greet the customer by saying
you have reached SliceHouse Pizza after hours and the shop is currently
closed. Still help: answer questions from the menu, and take PRE-ORDERS
for tomorrow from 11:00 AM. A pre-order follows the same flow (items,
name, phone, address for delivery), and its action block must have
"preorder": true.
"""
    if open_orders:
        lines = ["OPEN ORDERS right now (use these for status questions):"]
        for order in open_orders:
            items = ", ".join(
                f"{i['qty']}x {i.get('size') + ' ' if i.get('size') else ''}{i['name']}"
                for i in order.get("items") or []
            )
            lines.append(
                f"- {order.get('id')} — {order.get('name')} ({order.get('phone')}), "
                f"{items}, {order.get('type')}, status: {order.get('status')}"
            )
        base += "\n" + "\n".join(lines) + "\n"
    else:
        base += "\nOPEN ORDERS right now: none.\n"
    return base + "\nINFORMATION:\n" + KNOWLEDGE_BASE.strip() + "\n"
