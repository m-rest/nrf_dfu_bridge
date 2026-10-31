# nRF DFU Bridge

Flash an nRF52 board connected by USB to a macOS host while building firmware in a Linux development container. The bridge forwards the board's USB serial connection over RFC2217/TCP and uploads firmware using Nordic Serial DFU.

The included example targets the **Seeed XIAO nRF52840 with its Nordic serial bootloader**. Other boards may need different USB identifiers, reset handling or DFU metadata.

## How it works

```text
macOS host                              Linux development container
──────────                              ───────────────────────────
USB-connected nRF52                     PlatformIO project
    │                                       │
    ▼                                       ▼
nrf_serial_agent.py  ◄── RFC2217/TCP ──► nrf_dfu_upload.py
    │                                       │
    ▼                                       ▼
USB serial port                       ELF → HEX → Nordic DFU ZIP
```

The Mac agent owns the physical USB serial port and exposes it through an RFC2217 TCP endpoint. The container-side uploader builds a Nordic DFU package and sends it through that endpoint.

The uploader uses `NRF_DFU_BRIDGE_URL` when set. Otherwise, it uses the URL in the board JSON. This keeps runtime-specific host-gateway details out of the shared uploader code.

## Requirements

### macOS host

- macOS with Python 3 and `venv` (Python 3.9 or newer)
- The target board connected by USB
- Permission to access the board's USB serial device
- A network route from the container to the Mac agent's TCP port (default: `2217`)

The Mac agent uses `pyserial==3.5`. The installer creates an isolated Python virtual environment.

### Linux development container

- Linux with Bash
- Python 3.10 or newer, including `venv` and `pip`
- `curl`, `tar`, CA certificates and `git`
- Network access to GitHub and the PlatformIO package registry

On Debian/Ubuntu-based containers, install the prerequisites with:

```bash
sudo apt-get update
sudo apt-get install -y bash ca-certificates curl git python3 python3-pip python3-venv tar
```

If PlatformIO Core is not already installed, the container installer creates an isolated environment at `~/.platformio/penv` and installs PlatformIO Core 6.2.0. It also installs `pyserial==3.5` into the Python environment used by PlatformIO.

## Installation

### 1. Install the bridge in your Linux container

Run this from the root of your existing PlatformIO project:

```bash
curl -fsSL https://raw.githubusercontent.com/m-rest/nrf_dfu_bridge/main/install-container.sh \
  | NRF_DFU_BRIDGE_REPOSITORY=m-rest/nrf_dfu_bridge bash
```

The bridge files are installed into:

```text
<your-platformio-project>/.nrf-dfu-bridge/
```

If the installer creates the PlatformIO environment, add its CLI to your `PATH`:

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
```

To make this permanent, add the export to your shell startup file. If PlatformIO uses a non-default Python interpreter, set `PLATFORMIO_PYTHON` when running the installer. The installer prints the selected interpreter and installation directory.

### 2. Install the Mac agent

Run this on the Mac:

```bash
curl -fsSL https://raw.githubusercontent.com/m-rest/nrf_dfu_bridge/main/mac-agent/install-mac-agent.sh | bash
```

The installer downloads the agent, board profile, requirements and launcher from the repository's `main` branch. It then:

- Installs the agent and its virtual environment under `~/.local/share/nrf-dfu-bridge/`
- Creates `~/.config/nrf-dfu-bridge/xiao_nrf52840.json` only if it does not already exist
- Installs the launcher at `~/.local/bin/nrf-dfu-bridge`

An existing local configuration is kept; the installer does not overwrite it. The installer requires Python 3.9 or newer and `curl`.

Start the agent from a terminal:

```bash
~/.local/bin/nrf-dfu-bridge
```

If `~/.local/bin` is not in your `PATH`, add this to `~/.zprofile` and open a new terminal:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Keep the agent running while building and uploading firmware.

## Configuration

The board profile is JSON:

- In the container: `.nrf-dfu-bridge/boards/xiao_nrf52840.json`
- On the Mac: `~/.config/nrf-dfu-bridge/xiao_nrf52840.json`

The Mac installer initially copies the repository's board profile to the local configuration path. The two files are separate copies: update the settings on the side where they are used. The container uploader reads the project copy; the Mac agent reads the Mac-local copy.

The profile contains:

- `connection`: RFC2217 URL, normal baud rate and 1200-baud reset rate
- `usb`: application and bootloader USB VID/PID, plus optional serial-number and bootloader-port filters
- `agent`: Mac listener address, TCP port and USB-device polling timings
- `dfu`: backend and Nordic DFU metadata

### Set the container-reachable RFC2217 URL

The example profile contains a placeholder URL:

```json
"url": "rfc2217://YOUR_HOST_GATEWAY:2217"
```

Set `NRF_DFU_BRIDGE_URL` in the container to the address that the container can actually reach. For example:

```bash
export NRF_DFU_BRIDGE_URL="rfc2217://host.lima.internal:2217"
```

Other common environments may use `host.docker.internal` or a host-gateway address configured for the container runtime. The correct hostname depends on your setup.

You can also set the variable for a single upload command:

```bash
NRF_DFU_BRIDGE_URL="rfc2217://host.lima.internal:2217" \
  pio run -e seeed_xiao_nrf52840_sense -t upload
```

### Configure USB device selection on the Mac

The public board profile does not contain a personal USB serial number or machine-specific device path. If needed, edit the Mac-local file:

```text
~/.config/nrf-dfu-bridge/xiao_nrf52840.json
```

Set `usb.serial_number` if the board reports the same USB serial number in application and bootloader modes. Alternatively, set `usb.bootloader_port` if macOS exposes a stable callout path for the bootloader. Both values are optional; leave them as `null` if VID/PID matching identifies the board reliably.

Example values:

```json
"serial_number": "YOUR_DEVICE_SERIAL_NUMBER",
"bootloader_port": "/dev/cu.YOUR_DEVICE_PORT"
```

To inspect serial devices and their USB details:

```bash
~/.local/share/nrf-dfu-bridge/venv/bin/python -m serial.tools.list_ports -v
```

### Agent listen address

The agent listens on `127.0.0.1:2217` by default. This is preferred when your container runtime can reach the host's loopback interface. If your setup requires another interface, change `agent.bind` in the Mac-local board configuration. Only do this on a trusted network; RFC2217 has no authentication or encryption.

## Configure PlatformIO

In the relevant environment in your project's `platformio.ini`, add:

```ini
upload_protocol = custom
extra_scripts = post:.nrf-dfu-bridge/scripts/platformio_upload.py
```

For the included Seeed XIAO nRF52840 example, the environment looks like this:

```ini
[env:seeed_xiao_nrf52840_sense]
platform = https://github.com/Seeed-Studio/platform-seeedboards.git
board = seeed-xiao-afruitnrf52-nrf52840
framework = arduino
monitor_speed = 115200
upload_protocol = custom
extra_scripts = post:.nrf-dfu-bridge/scripts/platformio_upload.py
platform_packages =
    toolchain-gccarmnoneeabi @ 1.140201.0
```

Keep your existing project settings and add the two upload settings to the appropriate environment.

PlatformIO downloads the Seeed platform and its declared build packages, including the ARM toolchain and Adafruit nrfutil, during the first build. You do not need to install those packages manually. The first build requires network access and may take longer than subsequent builds.

Build and upload from the project root:

```bash
pio run -e seeed_xiao_nrf52840_sense
pio run -e seeed_xiao_nrf52840_sense -t upload
```

Use the same PlatformIO Core installation from the CLI and the VS Code PlatformIO extension. If they use different Python environments, make sure `pyserial==3.5` is installed in the Python environment used by PlatformIO.

If you use a non-default PlatformIO Core directory, set `PLATFORMIO_CORE_DIR` consistently for PlatformIO and the uploader.

## Upload sequence

1. PlatformIO builds the firmware and produces `firmware.elf`.
2. The uploader converts the ELF file to Intel HEX using ARM `objcopy`.
3. Adafruit nrfutil creates the Nordic DFU ZIP using the board's DFU metadata.
4. The uploader opens the RFC2217 endpoint at 1200 baud to trigger the bootloader transition.
5. The Mac agent detects the bootloader USB interface and opens it for the next RFC2217 session.
6. Adafruit nrfutil transfers the DFU package at the configured baud rate.

## Troubleshooting

- **Connection refused:** Check that the Mac agent is running and that `NRF_DFU_BRIDGE_URL` points to the host address and port reachable from the container.
- **USB device not found:** Check the USB VID/PID values and, if necessary, configure `usb.serial_number` or `usb.bootloader_port` in the Mac-local board profile. The agent logs the detected USB serial devices when selection times out.
- **Upload works from the CLI but not from VS Code:** Check whether the extension uses a different PlatformIO Core or Python environment. Install `pyserial==3.5` into the environment used by that Core.
- **First build fails while downloading packages:** Check network access to GitHub and the PlatformIO package registry.

## Uninstall

On the Mac, download and run the uninstaller:

```bash
curl -fsSL https://raw.githubusercontent.com/m-rest/nrf_dfu_bridge/main/mac-agent/uninstall-mac-agent.sh \
  -o /tmp/uninstall-nrf-dfu-bridge.sh
bash /tmp/uninstall-nrf-dfu-bridge.sh
```

This removes the installed agent, virtual environment and launcher while keeping the local configuration. To remove the configuration as well, pass `--purge-config`:

```bash
bash /tmp/uninstall-nrf-dfu-bridge.sh --purge-config
```

The container-side files can be removed by deleting `.nrf-dfu-bridge/` from your PlatformIO project.

## Security

RFC2217 is unauthenticated and unencrypted. The agent binds to `127.0.0.1` by default. Do not bind it to `0.0.0.0` or expose TCP port `2217` to an untrusted network.

## Limitations

- The included board profile is intended for the Seeed XIAO nRF52840 with the supported Nordic serial bootloader.
- Only the `nordic_serial` DFU backend is implemented.
- Other nRF boards may require different USB identifiers, reset behavior, DFU metadata or upload handling, and need their own configuration and testing.
- Host-gateway names vary by container runtime. Set `NRF_DFU_BRIDGE_URL` to the endpoint reachable from your container.

## License

MIT. See [LICENSE](LICENSE).
