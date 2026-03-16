#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${SCRIPT_DIR}/.venv/bin/python"

if [[ ! -f "${VENV_PY}" ]]; then
  echo "ERROR: Virtual environment not found at ${SCRIPT_DIR}/.venv"
  echo "Run the installer first: bash Install_And_Run_NULLA.sh"
  exit 1
fi

export PYTHONPATH="${SCRIPT_DIR}"
export NULLA_HOME="${NULLA_HOME:-${HOME}/.nulla_runtime}"

echo "Starting Nulla Hive Mind..."
echo "API: http://127.0.0.1:11435"
exec "${VENV_PY}" -m apps.nulla_api_server
