#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="/Users/sauliuskruopis/Desktop/Decentralized_NULLA"
VENV_PY="/Users/sauliuskruopis/Desktop/Decentralized_NULLA/.venv/bin/python"
MODEL_TAG="${NULLA_OLLAMA_MODEL:-qwen2.5:32b}"
DEFAULT_NULLA_API_PORT="11435"
DEFAULT_OPENCLAW_PORT="18789"
ALT_NULLA_API_PORT="21435"
ALT_OPENCLAW_PORT="28790"

port_forwarded_by_ssh() {
  local port="$1"
  if ! command -v lsof >/dev/null 2>&1; then
    return 1
  fi
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | grep -q '[s]sh'
}

NULLA_API_PORT="${NULLA_OPENCLAW_API_PORT:-${DEFAULT_NULLA_API_PORT}}"
GW_PORT="${NULLA_OPENCLAW_GATEWAY_PORT:-${DEFAULT_OPENCLAW_PORT}}"
if [[ -z "${NULLA_OPENCLAW_API_PORT:-}" ]] && port_forwarded_by_ssh "${NULLA_API_PORT}"; then
  NULLA_API_PORT="${ALT_NULLA_API_PORT}"
fi
if [[ -z "${NULLA_OPENCLAW_GATEWAY_PORT:-}" ]] && port_forwarded_by_ssh "${GW_PORT}"; then
  GW_PORT="${ALT_OPENCLAW_PORT}"
fi

OPENCLAW_PROFILE="${NULLA_OPENCLAW_PROFILE:-}"
if [[ -z "${OPENCLAW_PROFILE}" ]] && { [[ "${NULLA_API_PORT}" != "${DEFAULT_NULLA_API_PORT}" ]] || [[ "${GW_PORT}" != "${DEFAULT_OPENCLAW_PORT}" ]]; }; then
  OPENCLAW_PROFILE="nulla-host"
fi
OPENCLAW_HOME_OVERRIDE="${NULLA_OPENCLAW_HOME:-}"
if [[ -z "${OPENCLAW_HOME_OVERRIDE}" ]] && [[ -n "${OPENCLAW_PROFILE}" ]]; then
  OPENCLAW_HOME_OVERRIDE="${HOME}/.openclaw-${OPENCLAW_PROFILE}"
fi

OPENCLAW_NODE="${HOME}/.nvm/versions/node/v22.22.1/bin/node"
OPENCLAW_ENTRY="${HOME}/.nvm/versions/node/v20.20.0/lib/node_modules/openclaw/openclaw.mjs"
export PYTHONPATH="${PROJECT_ROOT}"
export NULLA_HOME="/Users/sauliuskruopis/.nulla_runtime"
export NULLA_OLLAMA_MODEL="${NULLA_OLLAMA_MODEL:-${MODEL_TAG}}"
export NULLA_OPENCLAW_API_PORT="${NULLA_API_PORT}"
export NULLA_OPENCLAW_API_URL="http://127.0.0.1:${NULLA_API_PORT}"
export NULLA_OPENCLAW_GATEWAY_PORT="${GW_PORT}"
if [[ -n "${OPENCLAW_PROFILE}" ]]; then
  export NULLA_OPENCLAW_PROFILE="${OPENCLAW_PROFILE}"
fi
if [[ -n "${OPENCLAW_HOME_OVERRIDE}" ]]; then
  export OPENCLAW_HOME="${OPENCLAW_HOME_OVERRIDE}"
fi
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

if ! curl -sf --max-time 2 "${NULLA_OPENCLAW_API_URL}/api/tags" >/dev/null 2>&1; then
  nohup "${VENV_PY}" -m apps.nulla_api_server --port "${NULLA_API_PORT}" >/tmp/nulla_api_server.log 2>&1 &
  for _ in $(seq 1 30); do
    sleep 1
    if curl -sf --max-time 2 "${NULLA_OPENCLAW_API_URL}/api/tags" >/dev/null 2>&1; then
      break
    fi
  done
fi

if [[ -n "${OPENCLAW_HOME_OVERRIDE}" ]]; then
  OPENCLAW_HOME="${OPENCLAW_HOME_OVERRIDE}" "${VENV_PY}" "${PROJECT_ROOT}/installer/register_openclaw_agent.py" \
    "${PROJECT_ROOT}" "${NULLA_HOME}" "${MODEL_TAG}" "NULLA" >/tmp/nulla_openclaw_register.log 2>&1 || true
else
  "${VENV_PY}" "${PROJECT_ROOT}/installer/register_openclaw_agent.py" \
    "${PROJECT_ROOT}" "${NULLA_HOME}" "${MODEL_TAG}" "NULLA" >/tmp/nulla_openclaw_register.log 2>&1 || true
fi

if ! curl -sf --max-time 2 "http://127.0.0.1:${GW_PORT}" >/dev/null 2>&1; then
  if [[ -x "${OPENCLAW_NODE}" ]] && [[ -f "${OPENCLAW_ENTRY}" ]]; then
    PROFILE_ARGS=()
    if [[ -n "${OPENCLAW_PROFILE}" ]]; then
      PROFILE_ARGS=(--profile "${OPENCLAW_PROFILE}")
    fi
    nohup "${OPENCLAW_NODE}" "${OPENCLAW_ENTRY}" "${PROFILE_ARGS[@]}" gateway run --force >/tmp/nulla_openclaw.log 2>&1 &
  elif command -v ollama >/dev/null 2>&1; then
    nohup ollama launch openclaw --model "${MODEL_TAG}" >/tmp/nulla_openclaw.log 2>&1 &
  fi
  for _ in $(seq 1 30); do
    sleep 1
    if curl -sf --max-time 2 "http://127.0.0.1:${GW_PORT}" >/dev/null 2>&1; then
      break
    fi
  done
fi

GW_TOKEN="$("${VENV_PY}" -c "import sys; sys.path.insert(0, '/Users/sauliuskruopis/Desktop/Decentralized_NULLA'); from core.openclaw_locator import load_gateway_token; print(load_gateway_token())" 2>/dev/null || true)"
OPENCLAW_URL="http://127.0.0.1:${GW_PORT}"
TRACE_URL="${NULLA_OPENCLAW_API_URL}/trace"
if [[ -n "${GW_TOKEN}" ]]; then
  OPENCLAW_URL="${OPENCLAW_URL}/#token=${GW_TOKEN}"
fi

if command -v open >/dev/null 2>&1; then
  open "${OPENCLAW_URL}" >/dev/null 2>&1 || true
  open "${TRACE_URL}" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${OPENCLAW_URL}" >/dev/null 2>&1 || true
  xdg-open "${TRACE_URL}" >/dev/null 2>&1 || true
fi

echo "NULLA running. OpenClaw URL: ${OPENCLAW_URL}"
echo "NULLA trace rail: ${TRACE_URL}"
if [[ -n "${OPENCLAW_PROFILE}" ]]; then
  echo "OpenClaw profile: ${OPENCLAW_PROFILE}"
fi
