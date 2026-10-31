#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$HOME/.local/share/nrf-dfu-bridge"
CONFIG_DIR="$HOME/.config/nrf-dfu-bridge"
BIN_DIR="$HOME/.local/bin"
VENV="$APP_DIR/venv"

mkdir -p "$APP_DIR" "$CONFIG_DIR" "$BIN_DIR"
cp "$REPO_ROOT/mac-agent/nrf_serial_agent.py" "$APP_DIR/nrf_serial_agent.py"
chmod +x "$APP_DIR/nrf_serial_agent.py"

if [ ! -f "$CONFIG_DIR/xiao_nrf52840.json" ]; then
    cp "$REPO_ROOT/boards/xiao_nrf52840.json" "$CONFIG_DIR/xiao_nrf52840.json"
    echo "Created local board config: $CONFIG_DIR/xiao_nrf52840.json"
else
    echo "Keeping existing local board config: $CONFIG_DIR/xiao_nrf52840.json"
fi

if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$REPO_ROOT/requirements-mac.txt"

cp "$REPO_ROOT/mac-agent/nrf-dfu-bridge" "$BIN_DIR/nrf-dfu-bridge"
chmod +x "$BIN_DIR/nrf-dfu-bridge"

echo
echo "Installation complete."
echo "1. Review: $CONFIG_DIR/xiao_nrf52840.json"
echo "2. Start:  $BIN_DIR/nrf-dfu-bridge"
echo "   Ensure $BIN_DIR is in your PATH, or use the full path above."
