"""SliceHouse Pizza — WhatsApp Shop Assistant (Demo).

A WhatsApp-style ordering assistant for a fictional demo pizza
restaurant. A customer chats (mic, when a Groq key is configured) or
types; the assistant answers only from the demo menu, takes orders,
summarises them, and places them only after the customer confirms.
Totals are computed deterministically by the app from the menu — the
agent never states a total. Part of Kifayat Irfan's Agent Reveal
series on LinkedIn.

Runs fully on this VM with zero owner-supplied keys: the LLM (SenseNova)
authenticates via the stored credential surrogate, text mode is a complete
way to talk to the assistant, and voice replies use keyless edge-tts. Mic
input via Groq Whisper is optional and activates only when GROQ_API_KEY
is present (Streamlit secrets or env).
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import streamlit as st

from agent import (
    chat_reply,
    groq_key_available,
    synthesize_speech,
    transcribe_audio,
)
from knowledge_base import (
    DELIVERY_ETA,
    PICKUP_ETA,
    PREORDER_ETA,
    SHOP_NAME,
    compute_totals,
    format_order_id,
    next_order_id,
    same_order,
)

PAGE_TITLE = "SliceHouse Pizza — WhatsApp Shop Assistant (Demo)"

WHATSAPP_CSS = """
<style>
.block-container { max-width: 680px; padding-top: 1rem; }
.wa-header {
  background: #075E54; color: #fff; border-radius: 12px 12px 0 0;
  padding: 14px 18px; margin-bottom: 0;
}
.wa-header .wa-name { font-size: 20px; font-weight: 700; }
.wa-header .wa-status { font-size: 13px; color: #d9fdd3; }
.wa-header .wa-demo { font-size: 12px; color: #cfe3de; margin-top: 4px; }
</style>
"""


def _secret(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except Exception:
        return None
    return str(value).strip() if value else None


def _init_state() -> None:
    st.session_state.setdefault("messages", [])  # {role, content, audio?}
    st.session_state.setdefault("orders", [])  # {id, name, phone, type, address, items, total, status, eta, placed_at}
    st.session_state.setdefault("closed", False)
    st.session_state.setdefault("status", "Ready")
    st.session_state.setdefault("last_audio_hash", "")
    # Mic reset counter: the audio_input widget keeps its recorded audio as
    # its value after a recording, and re-using it then lands in the
    # widget's "An error has occurred, please try again." state instead of
    # a clean recorder. Bumping this counter changes the widget's key after
    # every processed recording, so Streamlit mounts a brand-new recorder
    # — the button looks exactly like the first time, every time.
    st.session_state.setdefault("wa_mic_nonce", 0)
    # Step-1 input row (see below): last text sent, and text queued by the
    # Enter/button callbacks for the plain _respond() send.
    st.session_state.setdefault("wa_last_sent", "")
    st.session_state.setdefault("wa_pending", "")
    # Last confirmed order (with totals), kept in session state so the
    # confirmation card survives Streamlit reruns (a bare st.success()
    # vanishes on rerun).
    st.session_state.setdefault("last_order", None)


def _items_text(items: list[dict]) -> str:
    return ", ".join(
        f"{i['qty']}x {(i.get('size') + ' ') if i.get('size') else ''}{i['name']}"
        for i in items
    )


def _respond(user_text: str) -> None:
    """Run one full turn: LLM reply, voice synthesis, order capture."""
    st.session_state.messages.append({"role": "user", "content": user_text})
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]
    open_orders = [
        o for o in st.session_state.orders if o["status"] in ("Preparing", "Pre-order")
    ]
    st.session_state.status = "Thinking…"
    try:
        with st.spinner("Thinking…"):
            display, order = chat_reply(
                history,
                closed=st.session_state.closed,
                open_orders=open_orders,
                sensenova_key=_secret("SENSENOVA_API_KEY"),
            )
    except Exception as exc:  # noqa: BLE001 - show the failure, keep the chat alive
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Sorry, I had trouble reaching the assistant just now ({exc}). Please try again."}
        )
        st.session_state.status = "Ready"
        return

    audio_bytes = None
    audio_format = "audio/mp3"
    st.session_state.status = "Speaking…"
    try:
        with st.spinner("Speaking…"):
            audio_bytes, audio_format = synthesize_speech(display)
    except Exception:  # noqa: BLE001 - text reply still stands without audio
        audio_bytes = None

    st.session_state.messages.append(
        {"role": "assistant", "content": display, "audio": audio_bytes,
         "audio_format": audio_format}
    )
    if order:
        totals = compute_totals(order["items"], order["type"])
        # Same customer re-confirming the same order must not create a
        # duplicate row.
        existing = None
        for candidate in st.session_state.orders:
            if same_order(candidate, order):
                existing = candidate
                break
        if existing is not None:
            st.session_state.last_order = existing
        else:
            number = next_order_id([o["number"] for o in st.session_state.orders])
            preorder = bool(order.get("preorder")) or st.session_state.closed
            record = {
                "number": number,
                "id": format_order_id(number),
                "name": order["name"],
                "phone": order["phone"],
                "type": order["type"],
                "address": order["address"],
                "items": order["items"],
                "items_text": _items_text(order["items"]),
                "subtotal": totals["subtotal"],
                "delivery_fee": totals["delivery_fee"],
                "total": totals["total"],
                "status": "Pre-order" if preorder else "Preparing",
                "eta": PREORDER_ETA if preorder else (
                    DELIVERY_ETA if order["type"] == "delivery" else PICKUP_ETA
                ),
                "placed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            st.session_state.orders.append(record)
            st.session_state.last_order = record
    st.session_state.status = "Ready"


def _order_summary_rows(order: dict) -> list[dict]:
    """Line-item rows for the confirmation card, with deterministic prices."""
    from knowledge_base import item_price

    rows = []
    for item in order["items"]:
        price = item_price(item["name"], item.get("size")) or 0.0
        label = (item.get("size") + " " if item.get("size") else "") + item["name"]
        rows.append({
            "Item": label,
            "Qty": item["qty"],
            "Price": f"${price:.2f}",
            "Line total": f"${price * item['qty']:.2f}",
        })
    rows.append({"Item": "Subtotal", "Qty": "", "Price": "", "Line total": f"${order['subtotal']:.2f}"})
    rows.append({"Item": "Delivery fee", "Qty": "", "Price": "", "Line total": f"${order['delivery_fee']:.2f}"})
    rows.append({"Item": "Total", "Qty": "", "Price": "", "Line total": f"${order['total']:.2f}"})
    return rows


def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, page_icon="🍕")
    _init_state()
    st.markdown(WHATSAPP_CSS, unsafe_allow_html=True)

    # --- WhatsApp-style header ------------------------------------------
    st.markdown(
        f"""<div class="wa-header">
        <div class="wa-name">🍕 {SHOP_NAME}</div>
        <div class="wa-status">🟢 online — replies instantly</div>
        <div class="wa-demo">DEMO — fictional restaurant, fictional menu and prices.
        No real orders, payments, or customer data. Part of Kifayat Irfan's
        Agent Reveal series.</div>
        </div>""",
        unsafe_allow_html=True,
    )

    st.write(f"**Assistant status:** {st.session_state.status}")

    # --- Sidebar: hours, delivery info, closed toggle -------------------
    with st.sidebar:
        st.subheader(f"🍕 {SHOP_NAME}")
        st.write("**Hours:** Mon–Sun, 11:00 AM – 10:00 PM")
        st.write("**Delivery:** 30–45 min · $2.99 · FREE over $30")
        st.write("**Pickup:** ready in ~20 min")
        if st.button(
            "🌙 Simulate closed hours"
            if not st.session_state.closed
            else "☀️ Switch back to open hours"
        ):
            st.session_state.closed = not st.session_state.closed
            mode = "closed" if st.session_state.closed else "open"
            note = (
                "The shop is now closed — taking pre-orders for tomorrow "
                "from 11:00 AM."
                if st.session_state.closed
                else "The shop is open again."
            )
            st.session_state.messages.append(
                {"role": "assistant", "content": f"[Demo] Shop is {mode}. {note}"}
            )
            st.rerun()
        if st.button("🗑️ Clear chat"):
            st.session_state.messages = []
            st.session_state.last_audio_hash = ""
            st.session_state.wa_mic_nonce += 1
            st.rerun()

    # --- Transcript -----------------------------------------------------
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🍕"):
            st.write(msg["content"])
            if msg["role"] == "assistant" and msg.get("audio"):
                st.audio(
                    msg["audio"],
                    format=msg.get("audio_format", "audio/mp3"),
                    autoplay=True,
                )

    # --- Message input row, WhatsApp-style (Step 2) -----------------------
    # One row: text field | mic button | send button. The mic is Streamlit's
    # native audio input sitting next to the message box, like WhatsApp.
    # Sending still goes through the plain _respond(). This step only moves
    # the mic into the row: if Cloud hangs after this step, the culprit is
    # the audio_input-in-columns placement; if it answers, the full row is
    # safe (the earlier hang then points at the resumable-reply logic,
    # which is NOT present here).
    #
    # Compact mic button: the native recorder renders as a tall, wide box
    # (waveform + 00:00 timer), much bigger than the send button. Hide the
    # waveform + timer and centre the round record button, so the mic is
    # the same height as the text field and send button. The record button
    # still turns into a stop button while recording, so the recording
    # state stays visible; the reply players (stAudio) are untouched.
    st.markdown(
        """
        <style>
        [data-testid="stAudioInput"] > div {
            min-height: 40px !important;
            height: 40px !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
            justify-content: center !important;
        }
        [data-testid="stAudioInputWaveSurfer"],
        [data-testid="stAudioInputWaveformTimeCode"] {
            display: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    groq_key = _secret("GROQ_API_KEY")
    voice_on = groq_key_available(groq_key)
    if not voice_on:
        st.info(
            "Voice input needs a Groq API key, which is not configured on this VM — "
            "so the microphone is off. Type your message below instead: the assistant "
            "works fully in text mode, with the same voice replies."
        )

    def _queue_typed() -> None:
        """Move the current text into wa_pending and clear the box.

        Runs as an on_change/on_click callback — i.e. before the text_input
        widget is instantiated — which is the only point where Streamlit
        allows a widget's session-state value to be changed.
        """
        text = (st.session_state.get("wa_typed") or "").strip()
        if (
            text
            and text != st.session_state.wa_last_sent
            and not st.session_state.get("wa_pending")
        ):
            st.session_state.wa_pending = text
            st.session_state.wa_last_sent = text
            st.session_state.wa_typed = ""

    if voice_on:
        col_text, col_mic, col_send = st.columns([7, 1, 1])
    else:
        col_text, col_send = st.columns([9, 1])
        col_mic = None
    with col_text:
        st.text_input(
            "Message",
            placeholder="Type your message to SliceHouse Pizza…",
            label_visibility="collapsed",
            key="wa_typed",
            on_change=_queue_typed,
        )
    if col_mic is not None:
        with col_mic:
            # Fresh key per recording (wa_mic_nonce): after a recording is
            # processed the counter is bumped below, so the next run mounts
            # a brand-new recorder instead of re-using the stale one that
            # ends up in the "An error has occurred" state.
            audio = st.audio_input(
                "🎙️",
                label_visibility="collapsed",
                key=f"wa_mic_{st.session_state.wa_mic_nonce}",
            )
        if audio is not None:
            raw = audio.getvalue()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != st.session_state.last_audio_hash:
                st.session_state.last_audio_hash = digest
                st.session_state.status = "Listening…"
                try:
                    with st.spinner("Listening…"):
                        heard = transcribe_audio(raw, groq_key=groq_key)
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
                else:
                    if heard:
                        _respond(heard)
                # Reset the recorder for the next use: a fresh widget key
                # so the mic button looks exactly like the first time.
                st.session_state.wa_mic_nonce += 1
                st.rerun()
    with col_send:
        st.button(
            "➤",
            key="wa_send",
            help="Send message",
            use_container_width=True,
            on_click=_queue_typed,
        )

    # Send the queued text once (Enter in the text field, or the ➤ button).
    pending = st.session_state.get("wa_pending")
    if pending:
        st.session_state.wa_pending = ""
        _respond(pending)
        st.rerun()

    # --- Order confirmation card (persistent) ---------------------------
    if st.session_state.last_order:
        order = st.session_state.last_order
        st.success(
            f"Order confirmed: {order['id']} — {order['name']} "
            f"({order['type']}, {order['status']}). ETA: {order['eta']}."
        )
        st.dataframe(_order_summary_rows(order), width="stretch", hide_index=True)

    # --- Orders panel ----------------------------------------------------
    st.subheader("🧾 Orders")
    st.metric("Orders placed", len(st.session_state.orders))
    if st.session_state.orders:
        display_rows = [
            {
                "Order": o["id"],
                "Customer": o["name"],
                "Phone": o["phone"],
                "Items": o["items_text"],
                "Total": f"${o['total']:.2f}",
                "Type": o["type"],
                "Status": o["status"],
                "Placed": o["placed_at"],
            }
            for o in st.session_state.orders
        ]
        st.dataframe(display_rows, width="stretch", hide_index=True)
    else:
        st.write("No orders yet. Place an order in the chat above and it will appear here.")


if __name__ == "__main__":
    main()
