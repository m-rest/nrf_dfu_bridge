#!/usr/bin/env python3
"""Build a Nordic serial-DFU package and upload it over an RFC2217 bridge."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


# PlatformIO installs Adafruit nrfutil into its own package directory.
PLATFORMIO_CORE_DIR = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio")).expanduser()
NRFUTIL_SITE = PLATFORMIO_CORE_DIR / "packages/tool-adafruit-nrfutil/site-packages"
if NRFUTIL_SITE.is_dir() and str(NRFUTIL_SITE) not in sys.path:
    sys.path.insert(0, str(NRFUTIL_SITE))

import serial  # noqa: E402


def run(command: list[str]) -> None:
    print("+", " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, check=True)


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    if config.get("dfu", {}).get("backend") != "nordic_serial":
        raise RuntimeError("This uploader supports only the nordic_serial backend")
    return config


def main() -> int:
    if len(sys.argv) != 3:
        raise RuntimeError(
            "Usage: nrf_dfu_upload.py <platformio-source> <board-config.json>"
        )

    source = Path(sys.argv[1]).resolve()
    config = load_config(Path(sys.argv[2]).resolve())

    build_dir = source.parent
    elf = build_dir / "firmware.elf"
    hex_file = build_dir / "firmware.hex"
    zip_file = build_dir / "firmware.zip"

    if not elf.is_file():
        raise FileNotFoundError(f"PlatformIO ELF not found: {elf}")

    packages = PLATFORMIO_CORE_DIR / "packages"
    objcopy = packages / "toolchain-gccarmnoneeabi/bin/arm-none-eabi-objcopy"
    nrfutil = packages / "tool-adafruit-nrfutil/adafruit-nrfutil.py"

    if not objcopy.is_file():
        raise FileNotFoundError(f"ARM objcopy not found: {objcopy}")
    if not nrfutil.is_file():
        raise FileNotFoundError(f"Adafruit nrfutil not found: {nrfutil}")

    connection = config["connection"]
    dfu = config["dfu"]
    # Runtime-specific host-gateway addresses can be supplied without editing
    # the shared board template. Example: NRF_DFU_BRIDGE_URL=rfc2217://...
    url = os.environ.get("NRF_DFU_BRIDGE_URL", connection["url"])
    baudrate = int(connection["baudrate"])
    reset_baudrate = int(connection["reset_baudrate"])

    print("\n[1/4] Erzeuge Intel-HEX aus ELF", flush=True)
    run([str(objcopy), "-O", "ihex", str(elf), str(hex_file)])

    print("\n[2/4] Erzeuge Nordic-DFU-Paket", flush=True)
    run([
        sys.executable,
        str(nrfutil),
        "dfu",
        "genpkg",
        "--dev-type", str(dfu["device_type"]),
        "--sd-req", str(dfu["softdevice_id"]),
        "--application", str(hex_file),
        str(zip_file),
    ])

    print("\n[3/4] 1200-Baud-Reset über RFC2217", flush=True)
    reset = serial.serial_for_url(url, baudrate=reset_baudrate, timeout=1)
    try:
        time.sleep(0.1)
    finally:
        reset.close()

    # Preserve the timing used by the known-working XIAO setup.
    time.sleep(2.0)

    print("\n[4/4] Starte Nordic Serial DFU", flush=True)
    original_serial = serial.Serial

    def serial_factory(port=None, *args, **kwargs):
        if isinstance(port, str) and "://" in port:
            return serial.serial_for_url(port, *args, **kwargs)
        return original_serial(port, *args, **kwargs)

    serial.Serial = serial_factory

    from nordicsemi.__main__ import cli

    arguments = [
        "adafruit-nrfutil",
        "dfu",
        "serial",
        "-p", url,
        "-b", str(baudrate),
    ]
    if dfu.get("singlebank", False):
        arguments.append("--singlebank")
    arguments.extend(["-pkg", str(zip_file)])

    sys.argv = arguments
    cli()
    print("\nFirmware erfolgreich übertragen.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
