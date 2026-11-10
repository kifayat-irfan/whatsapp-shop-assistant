"""Build the LinkedIn demo video from real screenshots + real agent voice.

Scenes use the recorded screenshots (demo/s*.png) and the agent's exact
reply texts (menu excerpt only — the full menu reply is 50s of voice),
spoken by the same TTS voice the demo uses. Title/end cards are simple
text cards. Output: demo/slicehouse-whatsapp-assistant-demo-final.mp4
(1280x800, H.264 + AAC, faststart).
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import synthesize_speech  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "demo")
W, H = 1280, 800

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Exact agent words from demo/transcript.json (emojis stripped for TTS;
# the on-screen text still shows them). The menu voiceover is the first
# sentence of the reply — the full reply is 50s of listing.
T1 = ("Hey there! Here's our menu: Pizzas, Small 10 inch, Medium 12 inch, "
      "Large 14 inch — Margherita, 9, 12, 15 dollars; Pepperoni, 10, 13, 16 "
      "dollars; Veggie Supreme, 10, 13, 16 dollars.")
T2 = ("Perfect, Ali! Here's your order: 1 Large Pepperoni pizza and 1 "
      "Garlic Bread, for delivery to 12 Main Street. I'll place that now — "
      "thanks for choosing SliceHouse!")
T3 = ("Your order number 1024, 1 Large Pepperoni, 1 Garlic Bread, is "
      "currently being prepared! It should be on its way soon — delivery "
      "takes about 30 to 45 minutes.")
T4 = ("Hey! We're closed right now, but I can take a pre-order for "
      "tomorrow from 11 AM. A Medium Margherita for pickup sounds great — "
      "could you share your full name and phone number?")


def card(lines, path):
    """A plain WhatsApp-green title card. lines: [(text, size, color)]."""
    img = Image.new("RGB", (W, H), (7, 94, 84))
    draw = ImageDraw.Draw(img)
    total = sum(size + 24 for _, size, _ in lines)
    y = (H - total) // 2
    for text, size, color in lines:
        font = ImageFont.truetype(FONT_BOLD if size >= 40 else FONT_REG, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), text, font=font, fill=color)
        y += size + 24
    img.save(path)
    print("card:", path)


def synth(text, name):
    audio, fmt = synthesize_speech(text)
    ext = ".mp3" if fmt == "audio/mp3" else ".wav"
    raw = os.path.join(DEMO, name + ext)
    with open(raw, "wb") as f:
        f.write(audio)
    # Normalize to 44.1 kHz stereo WAV so every segment has identical
    # audio specs — the concat step breaks if scenes differ.
    path = os.path.join(DEMO, name + "-norm.wav")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", raw,
        "-ar", "44100", "-ac", "2", path,
    ], check=True)
    print("audio:", path, len(audio), "bytes")
    return path


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def segment(image, audio, out, hold=0.8):
    """One scene: still image for (audio length + hold), with the audio."""
    dur = duration(audio) + hold
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-loop", "1", "-t", f"{dur:.2f}", "-i", image,
        "-i", audio,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-shortest", out,
    ], check=True)
    print("segment:", out, f"{dur:.1f}s")


def silent_segment(image, out, seconds):
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-loop", "1", "-t", f"{seconds:.1f}", "-i", image,
        "-f", "lavfi", "-t", f"{seconds:.1f}", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-shortest", out,
    ], check=True)
    print("segment (silent):", out, f"{seconds}s")


def main():
    card([("SliceHouse Pizza — WhatsApp Shop Assistant", 48, (255, 255, 255)),
          ("Every unanswered message is a lost order.", 34, (217, 253, 211)),
          ("Agent Reveal · Live demo", 26, (180, 210, 200))],
         os.path.join(DEMO, "card-title.png"))
    card([("Agent Reveal", 54, (255, 255, 255)),
          ("Agents that do the work.", 34, (217, 253, 211)),
          ("Built with Groq Whisper · SenseNova · Python", 26, (180, 210, 200))],
         os.path.join(DEMO, "card-end.png"))

    a1 = synth(T1, "voice-t1")
    a2 = synth(T2, "voice-t2")
    a3 = synth(T3, "voice-t3")
    a4 = synth(T4, "voice-t4")

    segs = []
    title = os.path.join(DEMO, "seg-title.mp4")
    silent_segment(os.path.join(DEMO, "card-title.png"), title, 3.0)
    segs.append(title)

    s2 = os.path.join(DEMO, "seg-s2.mp4")
    segment(os.path.join(DEMO, "s2-menu.png"), a1, s2)
    segs.append(s2)

    s4 = os.path.join(DEMO, "seg-s4.mp4")
    segment(os.path.join(DEMO, "s4-order-confirmed.png"), a2, s4)
    segs.append(s4)

    s5 = os.path.join(DEMO, "seg-s5.mp4")
    segment(os.path.join(DEMO, "s5-status.png"), a3, s5)
    segs.append(s5)

    s6 = os.path.join(DEMO, "seg-s6.mp4")
    segment(os.path.join(DEMO, "s6-preorder.png"), a4, s6)
    segs.append(s6)

    end = os.path.join(DEMO, "seg-end.mp4")
    silent_segment(os.path.join(DEMO, "card-end.png"), end, 4.0)
    segs.append(end)

    concat_list = os.path.join(DEMO, "concat.txt")
    with open(concat_list, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    out = os.path.join(DEMO, "slicehouse-whatsapp-assistant-demo.mp4")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list, "-c", "copy", out,
    ], check=True)
    final = os.path.join(DEMO, "slicehouse-whatsapp-assistant-demo-final.mp4")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", out,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-movflags", "+faststart", final,
    ], check=True)
    print("VIDEO:", final)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size", "-show_entries", "stream=codec_type,width,height",
         "-of", "json", final],
        capture_output=True, text=True, check=True)
    print(probe.stdout)


if __name__ == "__main__":
    main()
