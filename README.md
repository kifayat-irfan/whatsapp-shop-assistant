# SliceHouse Pizza — WhatsApp Shop Assistant (Demo)

A working WhatsApp-style ordering assistant for a **fictional** demo
pizza restaurant, built as Agent Reveal #2 in Kifayat Irfan's series on
LinkedIn (Agent Reveal #1 was the BrightSmile Dental voice agent). A
customer chats (mic) or types; the assistant answers only from the demo
menu, takes orders, summarises them, and places them only after the
customer confirms. The business problem it solves: **missed messages =
lost orders** — small shops lose orders when they cannot reply instantly
on WhatsApp.

No real restaurant, no real prices, no real orders or customer data —
every fact in `knowledge_base.py` is invented demo data, clearly
labelled as such in the UI.

## Run it on this VM (no keys needed)

```bash
./run_local.sh
# then open http://localhost:8502
```

The app runs fully with zero owner-supplied keys on this VM:

| Piece | How it authenticates here |
|---|---|
| LLM — SenseNova `sensenova-6.8-flash-lite` | Stored `custom.sensenova` credential via the `dynamic_credentials` surrogate helper (Bearer token to `token.sensenova.ai` only) |
| STT — Groq Whisper `whisper-large-v3-turbo` | Optional. Only active when `GROQ_API_KEY` is set. Without it the mic is off and the UI says so; text mode works fully |
| TTS — voice replies | Layered: edge-tts (`en-US-AriaNeural`) first, offline Piper (`en_US-amy-medium`, model in `voices/`) as fallback |

## How the assistant behaves

- Friendly, casual WhatsApp shop assistant. Short replies (1–3 sentences).
- Answers **only** from the menu. Anything not on it gets an honest
  "not on our menu" + a similar suggestion — never an invented dish or
  price. **BBQ Chicken is sold out today**: the assistant says so and
  suggests Pepperoni or Veggie Supreme.
- **Never states a dollar total.** It may quote an individual menu price
  when asked, but it never adds prices up. The app computes the total
  deterministically from the menu and shows it in an order confirmation
  card (line items, subtotal, delivery fee, total, order ID, ETA).
- Order flow: items (size + quantity) → delivery or pickup → name,
  phone, address (delivery only) → summary → **confirmation before
  placing**. No order is placed without the customer saying yes. The
  placed order ends the assistant's reply with a machine-readable
  ```json action block, which the app parses, strips from the
  displayed/spoken text, and records in the orders panel.
- Order IDs start at **#1024**; status "Preparing", ETA 30–45 min
  (delivery) or 20 min (pickup). Delivery is $2.99, FREE on orders over
  $30. Identical orders (same name + phone + items + address) are
  deduped — no second row.
- **Order status lookup:** the app passes the current open orders into
  the LLM context, so "Where is my order?" is answered from real
  captured orders — or honestly "no order found".
- **Closed mode:** the sidebar toggle "Simulate closed hours" makes the
  assistant say the shop is closed and take **pre-orders** for tomorrow
  from 11:00 AM (status "Pre-order").
- Never claims to be human.

## Project layout

```
app.py                 Streamlit UI (WhatsApp-style chat, orders panel)
agent.py               LLM / STT / TTS logic (no Streamlit calls — testable)
knowledge_base.py      Fictional demo menu + prices + order math + system prompt
voices/                Piper voice model (en_US-amy-medium)
tests/smoke_test.py    Live tests: menu answers, order flow, no-invention,
                       sold-out, status lookup, closed mode, action-block
                       parsing, TTS audio, no-key mic path
tests/app_test.py      Deterministic unit tests: order math, dedupe, order
                       IDs, block parsing, repair extraction
run_local.sh           Start the app on this VM (port 8502)
requirements.txt
.streamlit/secrets.toml.example   Template only — no real values
```

## Deploy to Streamlit Cloud (future, optional)

1. Push this folder to a GitHub repo.
2. On Streamlit Cloud, create the app from the repo (`app.py`).
3. In the app's Secrets, add:
   - `GROQ_API_KEY` — for mic input (optional; text mode works without it)
   - `SENSENOVA_API_KEY` — for the LLM (required on Cloud, where the
     VM's stored credential is not available)
4. The app prefers Streamlit secrets, then env vars, then the local
   surrogate — so the same code runs in both places.

## Tests

```bash
.venv/bin/python tests/smoke_test.py
.venv/bin/python tests/app_test.py
```

## Known VM network notes

- edge-tts cannot connect from this VM: the egress proxy blocks
  websocket upgrades. The app therefore falls back to offline Piper
  here; edge-tts remains the first choice on normal networks.
- gTTS is rate-limited to HTTP 429 from this VM's shared egress IP, so
  it is not used.
- edge-tts also needs two small VM-specific fixes in `agent.py`: the
  egress proxy is passed explicitly, and its SSL context is pointed at
  the system default CA bundle (edge-tts pins certifi's bundle, which
  does not trust the proxy's CA).

## Lessons applied from the dental build (Agent Reveal #1)

- Action blocks are stripped in ALL emission shapes (fenced, bare,
  dangling `json` tag, double emission) so no JSON ever reaches the
  spoken/displayed text.
- A verbal-only confirmation ("Your order is confirmed") with no action
  block triggers a **deterministic** repair: details are extracted from
  the customer's own messages (name/phone/items/address) — never
  another LLM JSON-only call, which this model answers with an empty
  message (verified 2026-11-05).
- The "Order confirmed" card is persistent in session state (a bare
  `st.success()` vanishes on Streamlit rerun).
- The agent never computes totals; the app does, deterministically.

## Owner steps

- None to run it on this VM. Nothing is posted, deployed to the cloud,
  or sent to any real restaurant by this project.
