#!/usr/bin/env bash
set -euo pipefail

# Public repository coordinates can be overridden for forks or tagged releases.
REPOSITORY="${NRF_DFU_BRIDGE_REPOSITORY:-OWNER/REPOSITORY}"
REF="${NRF_DFU_BRIDGE_REF:-v0.1.0}"
TARGET_DIR="${NRF_DFU_BRIDGE_TARGET:-$PWD/.nrf-dfu-bridge}"

if [[ "$REPOSITORY" == "OWNER/REPOSITORY" ]]; then
    cat >&2 <<'EOF'
Set NRF_DFU_BRIDGE_REPOSITORY to the public GitHub repository before running
this installer, for example:
  NRF_DFU_BRIDGE_REPOSITORY=your-org/nrf-dfu-bridge bash
EOF
    exit 2
fi

for tool in curl tar python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "Missing required command: $tool" >&2
        exit 1
    }
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ARCHIVE="$TMP_DIR/source.tar.gz"
URL="https://codeload.github.com/${REPOSITORY}/tar.gz/refs/heads/${REF}"

# Tags are also accepted; GitHub's archive endpoint uses /refs/tags/ for tags.
if [[ "$REF" == v* || "$REF" == *.*.* ]]; then
    URL="https://codeload.github.com/${REPOSITORY}/tar.gz/refs/tags/${REF}"
fi

echo "Downloading ${REPOSITORY}@${REF} ..."
curl --fail --location --silent --show-error "$URL" -o "$ARCHIVE"
mkdir -p "$TMP_DIR/unpacked"
tar -xzf "$ARCHIVE" -C "$TMP_DIR/unpacked" --strip-components=1

[[ -f "$TMP_DIR/unpacked/scripts/nrf_dfu_upload.py" ]] || {
    echo "Downloaded archive does not look like nrf-dfu-bridge." >&2
    exit 1
}

mkdir -p "$TARGET_DIR"
rm -rf "$TARGET_DIR/scripts" "$TARGET_DIR/boards"
cp -R "$TMP_DIR/unpacked/scripts" "$TARGET_DIR/scripts"
cp -R "$TMP_DIR/unpacked/boards" "$TARGET_DIR/boards"

# Install pyserial into the Python environment used by PlatformIO.
PIO_PYTHON="${PLATFORMIO_PYTHON:-}"
if [[ -z "$PIO_PYTHON" ]] && command -v pio >/dev/null 2>&1; then
    PIO_LAUNCHER="$(command -v pio)"
    FIRST_LINE="$(head -n 1 "$PIO_LAUNCHER" 2>/dev/null || true)"
    if [[ "$FIRST_LINE" =~ ^#!(.*/python[0-9.]*)$ ]]; then
        PIO_PYTHON="${BASH_REMATCH[1]}"
    fi
fi

if [[ -z "$PIO_PYTHON" ]]; then
    # Use PlatformIO's conventional isolated Core environment if the CLI is
    # not installed yet. This avoids modifying the container's system Python.
    PIO_ENV="${PLATFORMIO_CORE_PYTHON_ENV:-$HOME/.platformio/penv}"
    python3 -m venv "$PIO_ENV"
    PIO_PYTHON="$PIO_ENV/bin/python"
    "$PIO_PYTHON" -m pip install --upgrade pip
    "$PIO_PYTHON" -m pip install "platformio==6.2.0"
fi

if ! "$PIO_PYTHON" -m pip --version >/dev/null 2>&1; then
    echo "pip is missing for $PIO_PYTHON. Install python3-venv/python3-pip or set PLATFORMIO_PYTHON." >&2
    exit 1
fi

"$PIO_PYTHON" -m pip install --upgrade pyserial==3.5 || {
    echo "Could not install pyserial into $PIO_PYTHON." >&2
    echo "Set PLATFORMIO_PYTHON to the Python interpreter used by PlatformIO Core." >&2
    exit 1
}

echo
echo "Bridge files installed in: $TARGET_DIR"
echo "Python used for pyserial: $PIO_PYTHON"
if [[ -x "$(dirname "$PIO_PYTHON")/pio" ]]; then
    echo "PlatformIO CLI: $(dirname "$PIO_PYTHON")/pio"
    case ":$PATH:" in
        *":$(dirname "$PIO_PYTHON"):"*) ;;
        *) printf 'Add it to PATH if needed: export PATH="%s:$PATH"\n' "$(dirname "$PIO_PYTHON")" ;;
    esac
fi
echo
echo "Add these lines to the relevant [env:...] section in platformio.ini:"
echo "  upload_protocol = custom"
echo "  extra_scripts = post:$TARGET_DIR/scripts/platformio_upload.py"
echo
echo "PlatformIO downloads the configured Seeed platform, ARM toolchain and platform packages on the first build."
echo "Set NRF_DFU_BRIDGE_URL to override the board template RFC2217 endpoint for your container runtime."
