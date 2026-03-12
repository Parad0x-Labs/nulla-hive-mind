#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --with-wheelhouse   Download runtime wheels into the bundle for offline-first installs on this platform.
  --with-liquefy      Vendor a local Liquefy checkout into the bundle if one is available.
  --embed-public-hive-auth  Embed the live public Hive bootstrap into the staged bundle. Sensitive: internal-only.
  --help, -h          Show this help.
EOF
}

WITH_WHEELHOUSE=0
WITH_LIQUEFY=0
EMBED_PUBLIC_HIVE_AUTH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-wheelhouse)
      WITH_WHEELHOUSE=1
      ;;
    --with-liquefy)
      WITH_LIQUEFY=1
      ;;
    --embed-public-hive-auth)
      EMBED_PUBLIC_HIVE_AUTH=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${PROJECT_ROOT}/build/installer"
STAGE_DIR="${BUILD_ROOT}/Decentralized_NULLA_Installer"
STAMP="$(date +%Y%m%d_%H%M%S)"
BASE_NAME="Decentralized_NULLA_Installer_${STAMP}"
CHECKSUM_FILE="${BUILD_ROOT}/${BASE_NAME}_SHA256SUMS.txt"
LIQUEFY_SOURCE=""
RELEASE_VERSION="${NULLA_RELEASE_VERSION:-0.4.0-closed-test}"
PUBLIC_HIVE_BUNDLE_LABEL="no"

mkdir -p "${BUILD_ROOT}"
rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}"

echo "[hygiene] Checking repo distribution hygiene"
python3 "${PROJECT_ROOT}/ops/repo_hygiene_check.py" >/dev/null 2>&1 || {
  echo "ERROR: repo hygiene check failed. Run: python3 ops/repo_hygiene_check.py" >&2
  python3 "${PROJECT_ROOT}/ops/repo_hygiene_check.py" || true
  exit 1
}

echo "[stage] Staging installer payload"
rsync -a \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '.cursor/' \
  --exclude '.nulla_local*/' \
  --exclude 'storage/*.db' \
  --exclude 'storage/*.db-shm' \
  --exclude 'storage/*.db-wal' \
  --exclude 'data/keys/*' \
  --exclude 'conftest.py' \
  --exclude 'test_data/' \
  --exclude 'tests/' \
  --exclude 'test_*.py' \
  --exclude 'AGENT_HANDOVER.md' \
  --exclude 'CURSOR_AUDIT_REPORT.md' \
  --exclude 'HEARTBEAT.md' \
  --exclude 'Cursor_Claude_Handover.md' \
  --exclude 'AGENTS.md' \
  --exclude 'docs/TDL.md' \
  --exclude 'docs/PROOF_PASS_REPORT.md' \
  --exclude 'docs/INTERNAL_*' \
  --exclude 'docs/MEET_AND_GREET_PREFLIGHT.md' \
  --exclude 'docs/MEET_AND_GREET_SERVER_ARCHITECTURE.md' \
  --exclude 'docs/MEET_AND_GREET_API_CONTRACT.md' \
  --exclude 'docs/MOBILE_CHANNEL_ROLLOUT_PLAN.md' \
  --exclude 'docs/MOBILE_CHANNEL_TEST_CHECKLIST.md' \
  --exclude 'docs/MOBILE_OPENCLAW_SUPPORT_ARCHITECTURE.md' \
  --exclude 'docs/OVERNIGHT_SOAK_RUNBOOK.md' \
  --exclude 'docs/BRAIN_HIVE_API_CONTRACT.md' \
  --exclude 'docs/BRAIN_HIVE_ARCHITECTURE.md' \
  --exclude 'docs/CLEAN_RUNTIME_SOAK_PREP.md' \
  --exclude 'docs/IMPLEMENTATION_STATUS.md' \
  --exclude 'docs/LAN_PROOF_CHECKLIST.md' \
  --exclude 'docs/LICENSING_MATRIX.md' \
  --exclude 'docs/MEET_AND_GREET_GLOBAL_TOPOLOGY.md' \
  --exclude 'docs/MODEL_INTEGRATION_POLICY.md' \
  --exclude 'docs/MODEL_PROVIDER_POLICY.md' \
  --exclude 'docs/UNICORN_ROADMAP.md' \
  --exclude 'docs/WHAT_WE_HAVE_NOW.md' \
  --exclude 'build/' \
  --exclude 'workspace/control/' \
  --exclude '.DS_Store' \
  "${PROJECT_ROOT}/" "${STAGE_DIR}/"

# workspace/control is generated runtime/operator state. Ship templates, not live queue/lease/run data.
rm -rf "${STAGE_DIR}/workspace/control"

mkdir -p "${STAGE_DIR}/vendor"

if [[ "${WITH_WHEELHOUSE}" -eq 1 ]]; then
  echo "[vendor] Downloading runtime wheelhouse"
  python3 -m pip download \
    --dest "${STAGE_DIR}/vendor/wheelhouse" \
    --prefer-binary \
    -r "${PROJECT_ROOT}/requirements-runtime.txt"
fi

if [[ "${WITH_LIQUEFY}" -eq 1 ]]; then
  if [[ -d "${PROJECT_ROOT}/vendor/liquefy-openclaw-integration" ]]; then
    LIQUEFY_SOURCE="${PROJECT_ROOT}/vendor/liquefy-openclaw-integration"
  elif [[ -d "${PROJECT_ROOT}/../liquefy-openclaw-integration" ]]; then
    LIQUEFY_SOURCE="${PROJECT_ROOT}/../liquefy-openclaw-integration"
  fi
  if [[ -z "${LIQUEFY_SOURCE}" ]]; then
    echo "ERROR: --with-liquefy was requested but no Liquefy checkout was found." >&2
    exit 1
  fi
  echo "[vendor] Bundling Liquefy payload from ${LIQUEFY_SOURCE}"
  rsync -a \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '.DS_Store' \
    "${LIQUEFY_SOURCE}/" "${STAGE_DIR}/vendor/liquefy-openclaw-integration/"
fi

if [[ "${EMBED_PUBLIC_HIVE_AUTH}" -eq 1 ]]; then
  echo "[bundle] Embedding live public Hive bootstrap into staged config"
  mkdir -p "${STAGE_DIR}/config"
  python3 "${PROJECT_ROOT}/ops/ensure_public_hive_auth.py" \
    --project-root "${STAGE_DIR}" \
    --target-path "${STAGE_DIR}/config/agent-bootstrap.json" \
    --require-auth \
    >/dev/null
  PUBLIC_HIVE_BUNDLE_LABEL="yes (internal auth embedded)"
else
  echo "[bundle] Public Hive auth not embedded. Auth-required clusters will stay read-only until runtime auth bootstrap succeeds."
fi

cat >"${STAGE_DIR}/BUNDLE_CONTENTS.txt" <<EOF
NULLA installer bundle

Bundled runtime wheelhouse: $( [[ "${WITH_WHEELHOUSE}" -eq 1 ]] && echo yes || echo no )
Bundled Liquefy payload: $( [[ "${WITH_LIQUEFY}" -eq 1 ]] && echo yes || echo no )
Bundled public Hive auth: ${PUBLIC_HIVE_BUNDLE_LABEL}

Fast path:
- Windows: double-click Install_And_Run_NULLA.bat
- Linux: run ./Install_And_Run_NULLA.sh
- macOS: open Install_And_Run_NULLA.command

Launchers now self-bootstrap on first run if .venv is missing.
If bundled public Hive auth is "no", the target machine can still run NULLA but public Hive writes
(create topic, claim task, post progress, submit result, presence export) will stay disabled until
runtime auth hydration succeeds from a bundled config or watch-node SSH bootstrap.
EOF

echo "[permissions] Normalizing launcher permissions"
for launch_path in \
  "${STAGE_DIR}/Install_And_Run_NULLA.sh" \
  "${STAGE_DIR}/Install_And_Run_NULLA.command" \
  "${STAGE_DIR}/Install_NULLA.sh" \
  "${STAGE_DIR}/Install_NULLA.command" \
  "${STAGE_DIR}/Stage_Trainable_Base.sh" \
  "${STAGE_DIR}/Stage_Trainable_Base.command" \
  "${STAGE_DIR}/OpenClaw_NULLA.sh" \
  "${STAGE_DIR}/OpenClaw_NULLA.command" \
  "${STAGE_DIR}/Start_NULLA.sh" \
  "${STAGE_DIR}/Start_NULLA.command" \
  "${STAGE_DIR}/Talk_To_NULLA.sh" \
  "${STAGE_DIR}/Talk_To_NULLA.command" \
  "${STAGE_DIR}/installer/install_nulla.sh"; do
  [[ -f "${launch_path}" ]] && chmod +x "${launch_path}" || true
done

echo "[archive] Building zip + tar.gz archives"
(
  cd "${BUILD_ROOT}"
  rm -f "${BASE_NAME}.zip" "${BASE_NAME}.tar.gz" "${BASE_NAME}.rar" "${CHECKSUM_FILE}"
  zip -qry "${BASE_NAME}.zip" "Decentralized_NULLA_Installer"
  tar -czf "${BASE_NAME}.tar.gz" "Decentralized_NULLA_Installer"
)

echo "[archive] Building .rar archive (optional)"
if command -v rar >/dev/null 2>&1; then
  (
    cd "${BUILD_ROOT}"
    rar a -idq "${BASE_NAME}.rar" "Decentralized_NULLA_Installer" >/dev/null
  )
  echo "RAR created: ${BUILD_ROOT}/${BASE_NAME}.rar"
else
  echo "RAR binary not found. ZIP/TAR.GZ produced instead."
fi

echo "[checksums] Writing SHA256 manifest"
BUILD_ROOT_ENV="${BUILD_ROOT}" BASE_NAME_ENV="${BASE_NAME}" python3 - <<'PY'
from __future__ import annotations

import hashlib
import os
from pathlib import Path

build_root = Path(os.environ["BUILD_ROOT_ENV"])
base = os.environ["BASE_NAME_ENV"]
targets = [build_root / f"{base}.zip", build_root / f"{base}.tar.gz", build_root / f"{base}.rar"]
lines = []
for path in targets:
    if not path.exists():
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    lines.append(f"{digest}  {path.name}")
(build_root / f"{base}_SHA256SUMS.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
PY

echo "[release] Refreshing latest platform aliases"
cp -f "${BUILD_ROOT}/${BASE_NAME}.zip" "${BUILD_ROOT}/Decentralized_NULLA_Windows_Latest.zip"
cp -f "${BUILD_ROOT}/${BASE_NAME}.zip" "${BUILD_ROOT}/Decentralized_NULLA_macOS_Latest.zip"
cp -f "${BUILD_ROOT}/${BASE_NAME}.tar.gz" "${BUILD_ROOT}/Decentralized_NULLA_Linux_Latest.tar.gz"
BUILD_ROOT_ENV="${BUILD_ROOT}" python3 - <<'PY'
from __future__ import annotations

import hashlib
import os
from pathlib import Path

build_root = Path(os.environ["BUILD_ROOT_ENV"])
targets = [
    build_root / "Decentralized_NULLA_Windows_Latest.zip",
    build_root / "Decentralized_NULLA_macOS_Latest.zip",
    build_root / "Decentralized_NULLA_Linux_Latest.tar.gz",
]
lines = []
for path in targets:
    if not path.exists():
        continue
    lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
(build_root / "Decentralized_NULLA_Latest_SHA256SUMS.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
PY

echo "[release] Writing real update manifest from latest installer bundle"
python3 "${PROJECT_ROOT}/ops/write_release_manifest.py" "${RELEASE_VERSION}" >/dev/null

echo
echo "Installer bundle ready:"
ls -lh "${BUILD_ROOT}/${BASE_NAME}.zip" "${BUILD_ROOT}/${BASE_NAME}.tar.gz" 2>/dev/null || true
ls -lh "${BUILD_ROOT}/${BASE_NAME}.rar" 2>/dev/null || true
ls -lh "${CHECKSUM_FILE}" 2>/dev/null || true
echo
echo "User flow:"
echo "1) Extract archive."
echo "2) Open extracted Decentralized_NULLA_Installer folder."
echo "3) Fast path: run Install_And_Run_NULLA.(command|sh|bat)."
echo "4) Launchers also self-bootstrap if .venv is missing."
