#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/.local/share/nrf-dfu-bridge"
CONFIG_DIR="$HOME/.config/nrf-dfu-bridge"
LAUNCHER="$HOME/.local/bin/nrf-dfu-bridge"

PURGE_CONFIG=0

usage() {
    cat <<'EOF'
Usage: uninstall-mac-agent.sh [--purge-config]

Removes the nRF DFU Bridge Mac agent, its virtual environment and launcher.

By default, keeps the local configuration under:
  ~/.config/nrf-dfu-bridge/

Options:
  --purge-config  Also delete the local configuration directory.
  -h, --help      Show this help.
EOF
}

while (($#)); do
    case "$1" in
        --purge-config)
            PURGE_CONFIG=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

echo "This will remove:"
echo "  $APP_DIR"
echo "  $LAUNCHER"
if ((PURGE_CONFIG)); then
    echo "  $CONFIG_DIR  (including local settings)"
else
    echo "The local configuration will be kept:"
    echo "  $CONFIG_DIR"
fi
echo

read -r -p "Continue? [y/N] " answer
case "$answer" in
    y|Y|yes|YES)
        ;;
    *)
        echo "Cancelled."
        exit 0
        ;;
esac

if [ -d "$APP_DIR" ]; then
    rm -rf -- "$APP_DIR"
    echo "Removed: $APP_DIR"
fi

if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
    rm -f -- "$LAUNCHER"
    echo "Removed: $LAUNCHER"
fi

if ((PURGE_CONFIG)) && [ -d "$CONFIG_DIR" ]; then
    rm -rf -- "$CONFIG_DIR"
    echo "Removed: $CONFIG_DIR"
fi

echo
echo "Uninstallation complete."
if ((!PURGE_CONFIG)); then
    echo "Local configuration was preserved."
    echo "To remove it later, rerun with --purge-config."
fi
