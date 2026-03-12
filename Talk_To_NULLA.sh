#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="/Users/sauliuskruopis/Desktop/Decentralized_NULLA"
VENV_PY="/Users/sauliuskruopis/Desktop/Decentralized_NULLA/.venv/bin/python"
export PYTHONPATH="${PROJECT_ROOT}"
export NULLA_HOME="/Users/sauliuskruopis/.nulla_runtime"
export PLAYWRIGHT_ENABLED="1"
export ALLOW_BROWSER_FALLBACK="1"
export BROWSER_ENGINE="chromium"
export WEB_SEARCH_PROVIDER_ORDER="searxng,ddg_instant,duckduckgo_html"
export SEARXNG_URL="${SEARXNG_URL:-http://127.0.0.1:8080}"
export NULLA_PUBLIC_HIVE_SSH_KEY_PATH="${NULLA_PUBLIC_HIVE_SSH_KEY_PATH:-}"
export NULLA_PUBLIC_HIVE_WATCH_HOST="${NULLA_PUBLIC_HIVE_WATCH_HOST:-161.35.145.74}"
"${VENV_PY}" "${PROJECT_ROOT}/ops/ensure_public_hive_auth.py" --project-root "${PROJECT_ROOT}" --watch-host "${NULLA_PUBLIC_HIVE_WATCH_HOST}" >/tmp/nulla_public_hive_auth.log 2>&1 || true
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  bash "/Users/sauliuskruopis/Desktop/Decentralized_NULLA/scripts/xsearch_up.sh" >/tmp/nulla_xsearch.log 2>&1 || true
fi
exec "${VENV_PY}" -m apps.nulla_chat --platform openclaw --device openclaw
