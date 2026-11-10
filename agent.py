"""Core agent logic for the SliceHouse Pizza WhatsApp shop assistant demo.

Providers:
- LLM: SenseNova (sensenova-6.8-flash-lite), OpenAI-compatible chat
  completions at https://token.sensenova.ai/v1/chat/completions.
  Key resolution order: explicit key (Streamlit secrets, passed in by the
  app) -> SENSENOVA_API_KEY env var -> stored custom.sensenova credential
  via the dynamic_credentials surrogate helper (local VM path only).
  The surrogate is only ever sent as a Bearer token to token.sensenova.ai;
  it is never printed, logged, or written to disk.
- STT: Groq Whisper (whisper-large-v3-turbo). Requires GROQ_API_KEY
  (Streamlit secrets or env). No key -> voice input is unavailable; the
  text path keeps working.
- TTS: edge-tts, voice en-US-AriaNeural, with offline Piper fallback.

This module makes no Streamlit calls, so it can be tested standalone.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import requests

from knowledge_base import (
    ITEM_ALIASES,
    build_system_prompt,
    canonical_item_name,
    is_pizza,
    normalize_items,
)

SENSENOVA_URL = "https://token.sensenova.ai/v1/chat/completions"
SENSENOVA_MODEL = "sensenova-6.8-flash-lite"
SENSENOVA_HOST = "token.sensenova.ai"
SENSENOVA_CREDENTIAL = "custom.sensenova"
DYNAMIC_CREDENTIALS_DIR = "/opt/muse/skills/custom-connector-setup/bin"

GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"

TTS_VOICE = "en-US-AriaNeural"

ACTION_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _bare_action_spans(text: str) -> list[tuple[int, int]]:
    """Spans of bare {"action": "order", ...} objects, brace-matched.

    A regex cannot match the nested braces of the items array (each item
    is itself an object), so the span is found with a depth counter that
    is aware of JSON strings.
    """
    spans = []
    for match in re.finditer(r'\{\s*"action"\s*:\s*"order"', text):
        start = match.start()
        depth = 0
        in_str = False
        escaped = False
        i = start
        while i < len(text):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        spans.append((start, i + 1))
                        break
            i += 1
    return spans


# ---------------------------------------------------------------- SenseNova

def _sensenova_request(payload: dict, explicit_key: str | None, timeout: int):
    """POST a chat-completions payload to SenseNova.

    Uses a real key (Streamlit secrets / env) when one is supplied, and the
    stored-credential surrogate helper otherwise (local VM path). The
    surrogate path never touches the credential value in this code.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(SENSENOVA_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    key = (explicit_key or "").strip() or os.environ.get("SENSENOVA_API_KEY", "").strip()
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    else:
        # Local-VM path only: the stored-credential surrogate helper lives
        # on this VM (/opt/muse/skills/...) and does NOT exist on Streamlit
        # Cloud. There the key must come from Streamlit Secrets, so a
        # missing helper here means the key was never configured — say so
        # plainly instead of leaking a confusing import error to the user.
        try:
            sys.path.insert(0, DYNAMIC_CREDENTIALS_DIR)
            from dynamic_credentials import add_surrogate_to_request
        except ImportError as exc:
            raise RuntimeError(
                "SenseNova API key is not configured. Add SENSENOVA_API_KEY "
                "to the app's Streamlit Secrets and redeploy."
            ) from exc

        add_surrogate_to_request(
            req, SENSENOVA_CREDENTIAL, allowed_hosts=[SENSENOVA_HOST]
        )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # A 401/403 here means the request itself: the credential was
        # attached (helper) or a real key was sent, so re-check the
        # request before blaming the key.
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"SenseNova request failed: HTTP {exc.code}: {detail}"
        ) from exc


def _order_candidate(obj: dict) -> dict | None:
    """Build the order dict from a parsed action object, or None.

    Items are normalised against the menu: unknown dishes are dropped,
    and the order is rejected entirely when no known item remains.
    Delivery orders need an address.
    """
    if not isinstance(obj, dict) or obj.get("action") != "order":
        return None
    raw_items = obj.get("items")
    if not isinstance(raw_items, list):
        return None
    items, _unknown = normalize_items(raw_items)
    if not items:
        return None
    order_type = str(obj.get("type") or "").strip().lower()
    if order_type not in ("delivery", "pickup"):
        return None
    name = str(obj.get("name") or "").strip()
    phone = str(obj.get("phone") or "").strip()
    address = str(obj.get("address") or "").strip()
    if not name or not phone:
        return None
    if order_type == "delivery" and not address:
        return None
    return {
        "name": name,
        "phone": phone,
        "type": order_type,
        "address": address,
        "items": items,
        "preorder": bool(obj.get("preorder")),
    }


def extract_order(text: str) -> tuple[str, dict | None]:
    """Strip every order action block; return (display_text, order).

    The model emits the block in several shapes, sometimes more than one
    at once: a fenced ```json block, a bare JSON object, a dangling `json`
    language tag with no backticks, or the JSON twice (fenced + bare).
    Every shape is removed from the display text; the first complete
    order found is returned. order is a dict with
    name/phone/type/address/items/preorder, or None when no complete
    order action is present.
    """
    order = None
    display = text

    # 1. Parse the first complete order from any fenced block.
    for match in ACTION_BLOCK_RE.finditer(display):
        try:
            obj = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        order = _order_candidate(obj)
        if order is not None:
            break
    # 2. Remove ALL fenced json blocks, parsed or not.
    display = ACTION_BLOCK_RE.sub("", display)

    # 3. Remove every bare action JSON object, parsing the first
    # complete order found along the way (covers the fenced + bare
    # double emission and the bare-only shape). A plain regex cannot
    # match the nested braces of the items array, so the spans come
    # from a brace-counting scan.
    for start, end in reversed(_bare_action_spans(display)):
        if order is None:
            try:
                obj = json.loads(display[start:end])
            except json.JSONDecodeError:
                obj = None
            order = _order_candidate(obj or {}) or order
        display = display[:start] + display[end:]

    # 4. Stray fences and dangling language tags left behind when a
    # block was emitted without its opening fence (the model sometimes
    # writes `json` as a bare word). The word "json" never legitimately
    # appears in a pizza shop assistant's reply, so removing a line
    # that is only that word is safe.
    display = display.replace("```", "")
    display = re.sub(r"(?m)^\s*json\s*$", "", display)

    # 5. A truncated second emission (cut off by the token limit) has
    # unbalanced braces, so no span matched it and the fragment above
    # survives. The action block is always the last thing in the reply,
    # so drop any leftover fragment from the first remaining '{"action"'
    # to the end of the text. The order itself was already captured.
    idx = display.find('{"action"')
    if idx != -1:
        display = display[:idx].rstrip()

    display = re.sub(r"\n{3,}", "\n\n", display).strip()
    return display, order


# ------------------------------------------------------- Deterministic repair

_PHONE_RE = re.compile(r"\b(\d{3}[- ]\d{3}[- ]\d{4})\b")
_NAME_RE = re.compile(
    r"\bmy name is\s+([A-Za-z][A-Za-z'’.-]*(?:\s+[A-Za-z][A-Za-z'’.-]*){0,3})", re.I
)
_ADDRESS_RES = (
    re.compile(r"\bmy address is\s+([^.\n]+)", re.I),
    re.compile(r"\bdeliver(?:y)? to\s+([^.\n]+)", re.I),
    re.compile(r"\baddress[:\s]+([^.\n]+)", re.I),
)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_QTY_DIGIT_RE = re.compile(
    r"(\d+)\s*(?:x\s*)?(?:small|medium|large)?\s*$", re.I
)
_QTY_WORD_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s*(?:small|medium|large)?\s*$",
    re.I,
)
_SIZE_RE = re.compile(r"\b(small|medium|large)\b", re.I)


def _extract_items_from_text(text: str) -> list[dict]:
    """Find menu items in free text with quantity and size.

    Looks at the words just before each item mention: a number (digit or
    word) optionally followed by a size word. Same item mentioned twice
    keeps the LAST mention's quantity (a repeated mention is the customer
    restating the order, not a second helping).
    """
    low = text.lower()
    found: dict[tuple[str, str | None], int] = {}
    for alias, canonical in ITEM_ALIASES.items():
        for match in re.finditer(r"\b" + re.escape(alias) + r"\b", low):
            window = low[max(0, match.start() - 40):match.start()]
            qty = 1
            digit = _QTY_DIGIT_RE.search(window)
            if digit:
                qty = int(digit.group(1))
            else:
                word = _QTY_WORD_RE.search(window)
                if word:
                    qty = _NUMBER_WORDS[word.group(1).lower()]
            size = None
            if is_pizza(canonical):
                sizes = _SIZE_RE.findall(window)
                if sizes:
                    size = sizes[-1].capitalize()
            found[(canonical, size)] = qty
    return [
        {"name": name, "size": size, "qty": qty}
        for (name, size), qty in found.items()
    ]


def _extract_address(text: str) -> str:
    for pattern in _ADDRESS_RES:
        match = pattern.search(text)
        if match:
            address = match.group(1).strip().strip(" .")
            # Stop at a trailing clause about the phone or name.
            address = re.split(
                r",?\s+and\s+(?:my\s+)?(?:phone|name)\b", address, flags=re.I
            )[0].strip()
            address = re.sub(r"\s+please\s*$", "", address, flags=re.I).strip()
            if address:
                return address
    return ""


def _repair_order(messages: list[dict]) -> dict | None:
    """Second-chance extraction when the agent confirmed an order
    without emitting a usable action block.

    Deterministic, not another LLM call: a JSON-only repair prompt makes
    this model answer with an empty message (one reasoning token, then
    stop — verified 2026-11-05), so the details are read straight from
    the CUSTOMER's own messages, the only trustworthy source. Returns
    the order dict, or None when any of name, phone, type, address
    (delivery), or items is missing.
    """
    user_texts = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if not user_texts:
        return None
    joined = " ".join(user_texts)
    low = joined.lower()

    name = None
    match = _NAME_RE.search(joined)
    if match:
        raw_name = match.group(1)
        raw_name = re.split(r",|\bmy phone\b|\band\b", raw_name, flags=re.I)[0].strip()
        name = raw_name.strip(" .")

    phones = _PHONE_RE.findall(joined)
    order_type = None
    if "pickup" in low or "pick up" in low:
        order_type = "pickup"
    elif "deliver" in low:
        order_type = "delivery"
    address = _extract_address(joined) if order_type == "delivery" else ""

    items, _unknown = normalize_items(_extract_items_from_text(joined))

    if not (name and phones and order_type and items):
        return None
    if order_type == "delivery" and not address:
        return None
    return {
        "name": name,
        "phone": phones[-1],
        "type": order_type,
        "address": address,
        "items": items,
        "preorder": False,
    }


def _sounds_confirmed(text: str) -> bool:
    """True when the reply reads like an order confirmation.

    The model sometimes confirms verbally ("Your order is confirmed")
    without emitting any action block at all — no fence, no JSON.
    That must still trigger the repair extraction, or the customer is
    told their order is placed while nothing is captured.
    """
    low = text.lower()
    return bool(re.search(r"\b(confirmed|placed|ordered)\b", low)) and any(
        word in low for word in ("order", "pizza", "delivery", "pickup")
    )


def chat_reply(
    messages: list[dict],
    closed: bool = False,
    open_orders: list[dict] | None = None,
    sensenova_key: str | None = None,
    timeout: int = 60,
) -> tuple[str, dict | None]:
    """One agent turn. messages: [{"role": "user"/"assistant", "content": str}].

    Returns (display_text, order_or_None). display_text never contains
    the machine-readable action block and never contains a dollar total.
    """
    payload = {
        "model": SENSENOVA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": build_system_prompt(closed=closed, open_orders=open_orders),
            }
        ]
        + [{"role": m["role"], "content": m["content"]} for m in messages],
        "temperature": 0.3,
        "max_tokens": 500,
    }
    data = None
    raw = ""
    for _attempt in range(2):
        data = _sensenova_request(payload, sensenova_key, timeout)
        try:
            raw = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Unexpected SenseNova response shape: {str(data)[:300]}"
            ) from exc
        if raw.strip():
            break
    if not raw.strip():
        # The model occasionally returns only its internal reasoning with
        # no reply text. Never speak the reasoning field; ask the customer
        # to try again instead.
        raise RuntimeError(
            "SenseNova returned an empty reply (no message content). "
            "Please try again."
        )
    display, order = extract_order(raw or "")
    if order is None and ("```" in (raw or "") or '"action"' in (raw or "")
                          or _sounds_confirmed(display)):
        # The agent started an action block but did not complete it, or
        # confirmed the order in words with no block at all: recover the
        # order deterministically from the customer's own messages.
        try:
            order = _repair_order(messages)
        except Exception:
            order = None
    return display, order


# ---------------------------------------------------------------- Groq Whisper

def groq_key_available(explicit_key: str | None = None) -> bool:
    return bool((explicit_key or "").strip() or os.environ.get("GROQ_API_KEY", "").strip())


def transcribe_audio(
    audio_bytes: bytes,
    groq_key: str | None = None,
    timeout: int = 60,
) -> str:
    """Transcribe WAV bytes with Groq Whisper. Raises if no key is set."""
    key = (groq_key or "").strip() or os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY is not configured, so voice input is unavailable. "
            "Type your message instead — the assistant works fully in text mode."
        )
    files = {"file": ("caller.wav", io.BytesIO(audio_bytes), "audio/wav")}
    data = {"model": GROQ_MODEL, "response_format": "json"}
    headers = {"Authorization": f"Bearer {key}"}
    resp = requests.post(
        GROQ_TRANSCRIPTIONS_URL, headers=headers, files=files, data=data, timeout=timeout
    )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


# ---------------------------------------------------------------- TTS
# Layered voice: edge-tts first (works on normal networks / Streamlit
# Cloud), Piper offline fallback (this VM's egress proxy blocks the
# websocket edge-tts needs, and gTTS is rate-limited to 429 there).
# Once an engine fails in-process, the working one is remembered so
# later turns don't re-pay the timeout. SHOP_ASSISTANT_TTS=edge|piper
# forces one engine. synthesize_speech returns (audio_bytes, mime_type).

_TTS_ENGINE = None  # None | "edge" | "piper"
_PIPER_VOICE = None

PIPER_MODEL_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
    "en/en_US/amy/medium/en_US-amy-medium.onnx"
)
PIPER_CONFIG_URL = PIPER_MODEL_URL + ".json"


def _synthesize_edge(text: str, timeout: int) -> bytes:
    """edge-tts (en-US-AriaNeural). Returns MP3 bytes."""
    import ssl

    import edge_tts
    import edge_tts.communicate as edge_comm

    # This VM's egress proxy terminates TLS with its own CA, trusted via
    # the system default CA bundle. edge-tts pins certifi's Mozilla-only
    # bundle, which does not trust that CA, so point its SSL context at
    # the system default instead. On hosts without the proxy this is the
    # normal system bundle and changes nothing.
    edge_comm._SSL_CTX = ssl.create_default_context()

    # edge-tts does not apply the proxy env vars to its websocket
    # connection on its own, so pass it explicitly when one is configured.
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")

    async def _run() -> bytes:
        communicate = edge_tts.Communicate(text, TTS_VOICE, proxy=proxy)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            path = tmp.name
        try:
            await asyncio.wait_for(communicate.save(path), timeout=timeout)
            with open(path, "rb") as handle:
                return handle.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    return asyncio.run(_run())


def _piper_model_path() -> str:
    env = os.environ.get("PIPER_MODEL_PATH", "").strip()
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "voices", "en_US-amy-medium.onnx")


def _ensure_piper_model() -> str:
    """Local Piper voice model; downloads it from HuggingFace if missing."""
    path = _piper_model_path()
    if os.path.exists(path) and os.path.exists(path + ".json"):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for url, dest in ((PIPER_MODEL_URL, path), (PIPER_CONFIG_URL, path + ".json")):
        resp = requests.get(url, timeout=300)
        resp.raise_for_status()
        with open(dest, "wb") as handle:
            handle.write(resp.content)
    return path


def _synthesize_piper(text: str) -> tuple[bytes, str]:
    """Offline Piper TTS. Returns (audio_bytes, mime_type), MP3 if ffmpeg
    is available, otherwise WAV."""
    global _PIPER_VOICE
    import wave

    from piper import PiperVoice

    if _PIPER_VOICE is None:
        _PIPER_VOICE = PiperVoice.load(_ensure_piper_model())
    voice = _PIPER_VOICE
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    mp3_path = wav_path + ".mp3"
    try:
        with wave.open(wav_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(voice.config.sample_rate)
            voice.synthesize_wav(text, wav_file)
        try:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", wav_path,
                 "-codec:a", "libmp3lame", "-q:a", "4", mp3_path],
                check=True, timeout=60,
            )
            with open(mp3_path, "rb") as handle:
                return handle.read(), "audio/mp3"
        except (subprocess.SubprocessError, OSError, FileNotFoundError):
            with open(wav_path, "rb") as handle:
                return handle.read(), "audio/wav"
    finally:
        for path in (wav_path, mp3_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def synthesize_speech(text: str, timeout: int = 25) -> tuple[bytes, str]:
    """Synthesise the reply. Returns (audio_bytes, mime_type).

    Tries edge-tts first; on failure falls back to offline Piper so the
    assistant still speaks on hosts where edge-tts cannot connect.
    """
    global _TTS_ENGINE
    forced = os.environ.get("SHOP_ASSISTANT_TTS", "").strip().lower()
    if forced == "edge":
        return _synthesize_edge(text, timeout), "audio/mp3"
    if forced == "piper":
        return _synthesize_piper(text)
    if _TTS_ENGINE == "piper":
        return _synthesize_piper(text)
    try:
        data = _synthesize_edge(text, timeout)
    except Exception:
        _TTS_ENGINE = "piper"
        return _synthesize_piper(text)
    _TTS_ENGINE = "edge"
    return data, "audio/mp3"
