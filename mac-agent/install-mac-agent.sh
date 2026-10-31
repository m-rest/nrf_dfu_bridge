#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$HOME/.local/share/nrf-dfu-bridge"
CONFIG_DIR="$HOME/.config/nrf-dfu-bridge"
VENV="$APP_DIR/venv"

mkdir -p "$APP_DIR" "$CONFIG_DIR"
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

cat > "$APP_DIR/start-agent.command" <<'COMMAND'
#!/bin/bash
set -euo pipefail
APP_DIR="$HOME/.local/share/nrf-dfu-bridge"
CONFIG="$HOME/.config/nrf-dfu-bridge/xiao_nrf52840.json"
"$APP_DIR/venv/bin/python" "$APP_DIR/nrf_serial_agent.py" --config "$CONFIG" -vv
COMMAND
chmod +x "$APP_DIR/start-agent.command"

echo
echo "Installation complete."
echo "1. Review: $CONFIG_DIR/xiao_nrf52840.json"
echo "2. Start:  open $APP_DIR/start-agent.command"
