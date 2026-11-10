#!/usr/bin/env bash
# launch_server.sh — run the SliceHouse Pizza WhatsApp shop-assistant
# Streamlit server as a transient systemd service.
#
# Why systemd, not setsid nohup: setsid relaunches died within ~5 minutes
# of the agent run that launched them (seen on the dental agent 2026-11-05
# at 16:06, 16:11, 16:16, 16:21 — four outages, each restart dead in ~5
# min). The agent run's cleanup evidently reaps its process tree/cgroup.
# A transient systemd service lives in system.slice, outside any agent
# run, so it survives — the same fix that cured the dental server and the
# demo bots. Transient units vanish on VM replacement, so the watchdog
# hook still relaunches then.
#
# Why the env forwarding: systemd services do NOT inherit the caller's
# env. Without the egress-proxy vars the agent's SenseNova calls fail.
# Values are taken from this shell's environment at launch time and passed
# via --setenv (never typed literally here).
set -u
DIR="$HOME/workspace/whatsapp-shop-assistant"
LOOP='export PYTHONUNBUFFERED=1; while true; do .venv/bin/streamlit run app.py --server.port 8502 --server.headless true >> server.log 2>&1; echo "$(date -u +%FT%TZ) streamlit exited, restarting in 10s" >> server.log; sleep 10; done'
ENV_ARGS=()
for v in HTTPS_PROXY HTTP_PROXY https_proxy http_proxy ALL_PROXY all_proxy NO_PROXY no_proxy REQUESTS_CA_BUNDLE SSL_CERT_FILE CURL_CA_BUNDLE; do
  if [ -n "${!v:-}" ]; then ENV_ARGS+=("--setenv=$v=${!v}"); fi
done
exec systemd-run --unit="shop-assistant" --service-type=simple --collect \
  --working-directory="$DIR" "${ENV_ARGS[@]}" \
  /bin/bash -c "$LOOP"
