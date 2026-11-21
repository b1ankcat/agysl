#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_BIN="${SCRIPT_DIR}/agysl.py"
SETTINGS_FILE="${AGY_SETTINGS_FILE:-${HOME}/.gemini/antigravity-cli/settings.json}"
TARGET_DIR="$(dirname "${SETTINGS_FILE}")"
INSTALLED_BIN="${AGYSL_INSTALL_PATH:-${TARGET_DIR}/agysl.py}"
STACK_WITH_DEFAULT="true"
ACTION="install"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Installs agysl.py into ~/.gemini/antigravity-cli/agysl.py (updating if present)
and configures Antigravity CLI statusLine stacked below the built-in status bar.

Options:
  --uninstall   Remove the statusLine entry from settings.json and installed agysl.py
  -h, --help    Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)
      ACTION="uninstall"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

mkdir -p "${TARGET_DIR}"

if [[ "${ACTION}" == "install" ]]; then
  if [[ ! -f "${SRC_BIN}" ]]; then
    echo "Source script not found: ${SRC_BIN}" >&2
    exit 1
  fi
  src_real="$(cd "$(dirname "${SRC_BIN}")" && pwd)/$(basename "${SRC_BIN}")"
  dst_real="$(cd "${TARGET_DIR}" && pwd)/$(basename "${INSTALLED_BIN}")"
  if [[ "${src_real}" != "${dst_real}" ]]; then
    cp -f "${SRC_BIN}" "${INSTALLED_BIN}"
  fi
  chmod +x "${INSTALLED_BIN}"
fi

python3 - "${SETTINGS_FILE}" "${INSTALLED_BIN}" "${STACK_WITH_DEFAULT}" "${ACTION}" <<'PY'
import json
import os
import shutil
import sys

settings_path, installed_bin, stack_str, action = sys.argv[1:5]
installed_bin = os.path.abspath(installed_bin)
stack = stack_str == "true"

os.makedirs(os.path.dirname(settings_path), exist_ok=True)

data = {}
if os.path.isfile(settings_path):
    shutil.copy2(settings_path, settings_path + ".bak")
    with open(settings_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if content:
            data = json.loads(content)

if action == "uninstall":
    data.pop("statusLine", None)
    if os.path.isfile(installed_bin):
        os.remove(installed_bin)
    msg = f"Removed statusLine from {settings_path} and deleted {installed_bin}"
else:
    data["statusLine"] = {
        "type": "command",
        "command": f"python3 {installed_bin}",
        "enabled": True,
        "padding": 0,
        "stack_with_default": stack,
    }
    msg = f"Installed {installed_bin} and configured statusLine in {settings_path} (stack_with_default={stack})"

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(msg)
PY
