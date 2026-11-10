#!/usr/bin/env bash
# Start the SliceHouse Pizza WhatsApp shop assistant demo on this VM.
# No keys needed: the LLM authenticates via the stored SenseNova
# credential (surrogate), text mode works fully, and voice replies use
# keyless edge-tts. Mic input activates only if GROQ_API_KEY is set.
set -e
cd "$(dirname "$0")"
if [ -x ".venv/bin/streamlit" ]; then
  exec .venv/bin/streamlit run app.py --server.port 8502 --server.headless true --browser.gatherUsageStats false
else
  exec python3 -m streamlit run app.py --server.port 8502 --server.headless true --browser.gatherUsageStats false
fi
