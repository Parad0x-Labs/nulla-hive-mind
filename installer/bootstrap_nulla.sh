#!/usr/bin/env bash
set -euo pipefail

OWNER="${NULLA_GITHUB_OWNER:-Parad0x-Labs}"
REPO="${NULLA_GITHUB_REPO:-nulla-hive-mind}"
REF="${NULLA_GITHUB_REF:-main}"
INSTALL_DIR="${NULLA_INSTALL_DIR:-$HOME/nulla-hive-mind}"
ARCHIVE_URL="${NULLA_ARCHIVE_URL:-https://github.com/${OWNER}/${REPO}/archive/refs/heads/${REF}.tar.gz}"
ARCHIVE_SHA256="${NULLA_ARCHIVE_SHA256:-}"
TMP_DIR=""
AUTO_START=1


say() {
  printf '%s\n' "$*"
}


cleanup() {
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}


usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --ref <git-ref>          Git branch or tag to fetch (default: ${REF})
  --dir <install-dir>      Target install folder (default: ${INSTALL_DIR})
  --archive-url <url>      Override the source archive URL
  --sha256 <hex>           Verify the downloaded archive against a SHA-256 digest
  --no-start               Install but do not launch NULLA
  --help, -h               Show this help

Environment overrides:
  NULLA_GITHUB_OWNER
  NULLA_GITHUB_REPO
  NULLA_GITHUB_REF
  NULLA_INSTALL_DIR
  NULLA_ARCHIVE_URL
  NULLA_ARCHIVE_SHA256
EOF
}


parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --ref requires a value."; exit 2; }
        REF="$1"
        ARCHIVE_URL="https://github.com/${OWNER}/${REPO}/archive/refs/heads/${REF}.tar.gz"
        ;;
      --dir)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --dir requires a value."; exit 2; }
        INSTALL_DIR="$1"
        ;;
      --archive-url)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --archive-url requires a value."; exit 2; }
        ARCHIVE_URL="$1"
        ;;
      --sha256)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --sha256 requires a value."; exit 2; }
        ARCHIVE_SHA256="$1"
        ;;
      --no-start)
        AUTO_START=0
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        say "ERROR: Unknown option: $1"
        usage
        exit 2
        ;;
    esac
    shift
  done
}


compute_sha256() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" | awk '{print $1}'
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 -r "${path}" | awk '{print $1}'
    return 0
  fi
  say "ERROR: Cannot verify archive checksum because sha256sum, shasum, and openssl are unavailable."
  exit 1
}


verify_archive_checksum() {
  local archive_path="$1"
  local expected
  expected="$(printf '%s' "${ARCHIVE_SHA256}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  if [[ -z "${expected}" ]]; then
    say "WARNING: Downloaded archive is not checksum-verified. Set --sha256 or NULLA_ARCHIVE_SHA256 to verify it."
    return 0
  fi
  local actual
  actual="$(compute_sha256 "${archive_path}")"
  if [[ "${actual}" != "${expected}" ]]; then
    say "ERROR: Archive checksum mismatch."
    say "Expected: ${expected}"
    say "Actual:   ${actual}"
    exit 1
  fi
  say "Archive checksum verified."
}


require_command() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    say "ERROR: Required command not found: ${cmd}"
    exit 1
  fi
}


prepare_install_dir() {
  mkdir -p "${INSTALL_DIR}"
  if [[ -f "${INSTALL_DIR}/Install_And_Run_NULLA.sh" || -f "${INSTALL_DIR}/installer/install_nulla.sh" || -f "${INSTALL_DIR}/install_nulla.sh" ]]; then
    say "Existing NULLA install detected at ${INSTALL_DIR}"
    return
  fi

  if find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 | read -r _; then
    say "ERROR: ${INSTALL_DIR} exists and is not an existing NULLA install."
    say "Choose an empty folder with --dir or remove the existing contents."
    exit 1
  fi
}


download_and_extract() {
  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT

  local archive_path="${TMP_DIR}/nulla.tar.gz"
  say "Downloading NULLA from ${ARCHIVE_URL}"
  curl -fsSL "${ARCHIVE_URL}" -o "${archive_path}"
  verify_archive_checksum "${archive_path}"

  say "Extracting to ${INSTALL_DIR}"
  tar -xzf "${archive_path}" --strip-components=1 -C "${INSTALL_DIR}"
}


launch_installer() {
  local launcher="${INSTALL_DIR}/Install_And_Run_NULLA.sh"
  local guided="${INSTALL_DIR}/Install_NULLA.sh"
  local canonical="${INSTALL_DIR}/installer/install_nulla.sh"
  if [[ ! -f "${canonical}" ]]; then
    canonical="${INSTALL_DIR}/install_nulla.sh"
  fi

  chmod +x "${launcher}" "${guided}" "${canonical}" 2>/dev/null || true

  say "Running NULLA installer..."
  if [[ "${AUTO_START}" -eq 1 ]]; then
    if [[ -f "${launcher}" ]]; then
      exec "${launcher}"
    fi
    if [[ -f "${canonical}" ]]; then
      exec "${canonical}" --yes --start --openclaw default
    fi
  fi
  if [[ -f "${guided}" ]]; then
    exec "${guided}" --yes --openclaw default
  fi
  if [[ -f "${canonical}" ]]; then
    exec "${canonical}" --yes --openclaw default
  fi
  say "ERROR: Bootstrap download succeeded, but no usable installer entrypoint was found."
  exit 1
}


main() {
  parse_args "$@"
  require_command curl
  require_command tar
  require_command bash
  prepare_install_dir
  download_and_extract
  launch_installer
}


main "$@"
