# nRF DFU Bridge

Upload firmware to an nRF52 board connected by USB to a macOS host while building from a Linux development container. The bridge transports the serial connection over RFC2217/TCP and uses Nordic Serial DFU.

**Included board example:** Seeed XIAO nRF52840 with the Nordic serial bootloader; the board definition and DFU metadata are board-specific.

## How it works

```text
macOS host                              Linux Dev Container
──────────                              ───────────────────
USB-connected nRF52                     PlatformIO project
    │                                       │
    ▼                                       ▼
nrf_serial_agent.py  ◄── RFC2217/TCP ──► nrf_dfu_upload.py
    │                                       │
    ▼                                       ▼
USB serial port                       ELF → HEX → Nordic DFU ZIP
```

The Mac agent exclusively owns the physical USB serial port and exposes it through an RFC2217 endpoint. The container-side uploader creates a Nordic DFU package and sends it through that endpoint. The uploader accepts the endpoint through `NRF_DFU_BRIDGE_URL`, so it does not need to know whether the container runs under Lima, Docker, Podman or another runtime.

## Requirements

### macOS host

- macOS with Python 3 and `venv`
- The target board connected by USB
- Permission to access its USB serial device
- A host/container network route that allows the Dev Container to reach the agent's TCP port (default `2217`)

The Mac agent requires only `pyserial==3.5`. Its installer creates an isolated virtual environment.

### Linux Dev Container

- Linux with Bash
- Python 3.10 or newer, including `venv` and `pip`
- `curl`, `tar`, and CA certificates
- `git` (PlatformIO may use it to fetch Git-based platforms)
- Network access to GitHub and the PlatformIO package registry

For Debian/Ubuntu-based containers, install the OS prerequisites with:

```bash
sudo apt-get update
sudo apt-get install -y bash ca-certificates curl git python3 python3-pip python3-venv tar
```

PlatformIO Core 6.2.0 is installed into an isolated environment by the installer if `pio` is not already available. The installer installs `pyserial==3.5` into the Python interpreter used by PlatformIO Core.

### Downloading the platform and build dependencies

You do **not** need to download the platform, ARM GCC toolchain, or Adafruit nrfutil manually. The XIAO example environment in `platformio.ini` points PlatformIO to Seeed's public `platform-seeedboards` Git repository. When you run the first PlatformIO build, PlatformIO fetches that platform and resolves/downloads its declared packages, including the ARM toolchain and the Adafruit nrfutil package used by the DFU uploader.

The first build therefore needs network access and can take longer than subsequent builds. PlatformIO caches the downloaded platform and packages in its Core directory (normally `~/.platformio`). If you use a non-default PlatformIO Core directory, set `PLATFORMIO_CORE_DIR` consistently for both PlatformIO and the uploader.

## Install into an existing PlatformIO project

Run this from the root of your existing PlatformIO firmware project.

```bash
curl -fsSL https://raw.githubusercontent.com/m-rest/nrf_dfu_bridge/main/install-container.sh \
  | NRF_DFU_BRIDGE_REPOSITORY=m-rest/nrf_dfu_bridge bash
```

This downloads the source archive. The bridge files are installed into:

```text
<your-platformio-project>/.nrf-dfu-bridge/
```

If PlatformIO Core is not installed, the script creates an isolated environment at `~/.platformio/penv` and installs PlatformIO Core 6.2.0 there. Add its CLI to your shell path:

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
```

If PlatformIO uses a non-default Python interpreter, set `PLATFORMIO_PYTHON=/path/to/python` before `bash` in the pipeline. The installer prints the selected interpreter and installation directory.

## Configure PlatformIO

In the environment used for the target board, add these settings while keeping your existing project options:

```ini
upload_protocol = custom
extra_scripts = post:.nrf-dfu-bridge/scripts/platformio_upload.py
```

### Seeed XIAO nRF52840 example

The following is a complete example for the supported XIAO target:

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

Build and upload from the project root:

```bash
pio run -e seeed_xiao_nrf52840_sense
pio run -e seeed_xiao_nrf52840_sense -t upload
```

Use the same PlatformIO Core installation for the CLI and the VS Code PlatformIO extension. If the extension uses a different Core/Python environment, install `pyserial==3.5` into that environment too or configure the extension to use the same Core.

## Configure the RFC2217 endpoint

The board template at `.nrf-dfu-bridge/boards/xiao_nrf52840.json` uses a placeholder host-gateway address:

```json
"url": "rfc2217://YOUR_HOST_GATEWAY:2217"
```

Host-gateway names vary by runtime and configuration. `host.docker.internal` is commonly used with Docker Desktop; Lima/Colima environments may provide `host.lima.internal`. On Linux Docker Engine, you may need to configure a host-gateway mapping or use the host's reachable address. The required value is the hostname/IP and port that the container can actually reach.

The Mac agent binds to `127.0.0.1` by default. This is appropriate when the container runtime forwards host loopback traffic to the host. If your runtime requires the agent to listen on another interface, change `agent.bind` in the Mac-local config only after considering the security implications. Do not expose the serial bridge to a public or untrusted network.

## Install the Mac agent

Download or check out the project on the Mac, then run:

```bash
./mac-agent/install-mac-agent.sh
```

The installer copies the agent to `~/.local/share/nrf-dfu-bridge/`, creates a local config at:

```text
~/.config/nrf-dfu-bridge/
```

It does not overwrite an existing local config. It creates a virtual environment and installs `pyserial==3.5`.

### Configure USB identification on macOS

The public board template contains no personal USB serial number or machine-specific device path. Edit the Mac-local config. Configure `serial_number` if the USB serial number is stable in both application and bootloader modes. If macOS uses a stable callout path across the transition, you can instead set `bootloader_port`. Both are optional; leave them `null` if VID/PID detection is sufficient.

Example values only:

```json
"serial_number": "YOUR_DEVICE_SERIAL_NUMBER",
"bootloader_port": "/dev/cu.YOUR_DEVICE_PORT"
```

List available serial devices with:

```bash
~/.local/share/nrf-dfu-bridge/venv/bin/python -m serial.tools.list_ports -v
```

Start the agent:

```bash
open ~/.local/share/nrf-dfu-bridge/start-agent.command
```

Or run it in a terminal:

```bash
~/.local/share/nrf-dfu-bridge/venv/bin/python \\
  ~/.local/share/nrf-dfu-bridge/nrf_serial_agent.py \\
  --config ~/.config/nrf-dfu-bridge/xiao_nrf52840.json -vv
```

Keep the agent running during firmware upload.

## Upload sequence

1. PlatformIO compiles the firmware and produces `firmware.elf`.
2. The uploader converts the ELF to Intel HEX using ARM `objcopy`.
3. Adafruit nrfutil creates the Nordic DFU ZIP using the board's configured device type and SoftDevice requirement.
4. The uploader opens the RFC2217 endpoint at 1200 baud to trigger the bootloader transition.
5. The Mac agent detects the bootloader USB interface and opens it for the next RFC2217 session.
6. Adafruit nrfutil transfers the DFU package at the configured baud rate.

## Board-specific configuration

`boards/xiao_nrf52840.json` is an example board profile. It contains:

- RFC2217 endpoint and baud rates
- Application and bootloader USB VID/PID
- Optional USB serial number and fixed bootloader port
- Mac agent bind address and timing
- Nordic DFU device type, SoftDevice requirement and single-bank setting

For another nRF52 board, create a separate board profile with the correct USB identifiers, reset behavior and Nordic DFU metadata. The uploader currently supports the `nordic_serial` backend only; a different bootloader may require a different backend. Feel free to contribute.

## Security

RFC2217 is unauthenticated and unencrypted. The agent binds to loopback (`127.0.0.1`) by default. Do not bind it to `0.0.0.0` or expose TCP port 2217 to an untrusted network.

## Limitations

- The included board profile is intended for the Seeed XIAO nRF52840 with the supported Nordic serial bootloader.
- Only Nordic Serial DFU is implemented.
- Other nRF boards may use different bootloaders, USB transitions, DFU metadata or upload protocols and need their own configuration and testing.
- Host-gateway names are runtime-specific. Set `NRF_DFU_BRIDGE_URL` to the endpoint reachable from your container.

## License

MIT. See [LICENSE](LICENSE).
