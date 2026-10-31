#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="${NRF_DFU_BRIDGE_REPOSITORY:-m-rest/nrf_dfu_bridge}"
REF="${NRF_DFU_BRIDGE_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY}/${REF}"

APP_DIR="$HOME/.local/share/nrf-dfu-bridge"
CONFIG_DIR="$HOME/.config/nrf-dfu-bridge"
BIN_DIR="$HOME/.local/bin"
VENV="$APP_DIR/venv"

if ! command -v curl >/dev/null 2>&1; then
    echo "Error: curl is required." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3.9 or newer is required." >&2
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "Error: Python 3.9 or newer is required." >&2
    python3 --version >&2
    exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nrf-dfu-bridge.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

download() {
    local path="$1"
    local destination="$2"
    echo "Downloading ${path}..."
    curl --fail --location --silent --show-error --retry 3 \
        "${RAW_BASE}/${path}" \
        --output "${destination}"
    if [[ ! -s "${destination}" ]]; then
        echo "Error: download was empty: ${path}" >&2
        exit 1
    fi
}

# Download and validate everything before changing the installation.
download "mac-agent/nrf_serial_agent.py" "$TMP_DIR/nrf_serial_agent.py"
download "boards/xiao_nrf52840.json" "$TMP_DIR/xiao_nrf52840.json"
download "requirements-mac.txt" "$TMP_DIR/requirements-mac.txt"
download "mac-agent/nrf-dfu-bridge" "$TMP_DIR/nrf-dfu-bridge"

python3 -m json.tool "$TMP_DIR/xiao_nrf52840.json" >/dev/null
chmod +x "$TMP_DIR/nrf-dfu-bridge"

mkdir -p "$APP_DIR" "$CONFIG_DIR" "$BIN_DIR"

install -m 644 "$TMP_DIR/nrf_serial_agent.py" "$APP_DIR/nrf_serial_agent.py"
install -m 755 "$TMP_DIR/nrf-dfu-bridge" "$BIN_DIR/nrf-dfu-bridge"

if [[ ! -f "$CONFIG_DIR/xiao_nrf52840.json" ]]; then
    install -m 644 "$TMP_DIR/xiao_nrf52840.json" "$CONFIG_DIR/xiao_nrf52840.json"
    echo "Created local board config: $CONFIG_DIR/xiao_nrf52840.json"
else
    echo "Keeping existing local board config: $CONFIG_DIR/xiao_nrf52840.json"
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    if [[ -e "$VENV" ]]; then
        echo "Error: $VENV exists but is not a working virtual environment." >&2
        echo "Remove that directory manually, then run the installer again." >&2
        exit 1
    fi
    python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$TMP_DIR/requirements-mac.txt"

echo
echo "Installation complete."
echo "1. Review: $CONFIG_DIR/xiao_nrf52840.json"
echo "2. Start:  $BIN_DIR/nrf-dfu-bridge"
echo "   Ensure $BIN_DIR is in your PATH, or use the full path above."
