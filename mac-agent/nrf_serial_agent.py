#!/usr/bin/env python3
"""Generic RFC2217 USB-serial bridge configured by a board JSON file."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import serial
import serial.rfc2217
from serial.tools import list_ports

LOG = logging.getLogger("nrf_serial_agent")


@dataclass(frozen=True)
class Device:
    device: str
    vid: Optional[int]
    pid: Optional[int]
    serial_number: Optional[str]
    description: str
    hwid: str
    manufacturer: Optional[str]
    product: Optional[str]
    interface: Optional[str]


class DeviceNotFound(Exception):
    pass


class AmbiguousDevice(Exception):
    pass


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    for section in ("connection", "usb", "agent"):
        if section not in config:
            raise ValueError(f"Missing config section: {section}")
    return config


def enumerate_devices() -> list[Device]:
    result = []
    for port in list_ports.comports():
        result.append(Device(
            device=port.device,
            vid=port.vid,
            pid=port.pid,
            serial_number=port.serial_number,
            description=port.description or "",
            hwid=port.hwid or "",
            manufacturer=getattr(port, "manufacturer", None),
            product=getattr(port, "product", None),
            interface=getattr(port, "interface", None),
        ))
    return result


def log_usb_inventory(devices: list[Device], reason: str) -> None:
    LOG.warning("USB inventory (%s): %d serial port(s)", reason, len(devices))
    for device in devices:
        vid = f"{device.vid:04x}" if device.vid is not None else "----"
        pid = f"{device.pid:04x}" if device.pid is not None else "----"
        LOG.warning(
            "  port=%r vid:pid=%s:%s serial=%r product=%r description=%r",
            device.device, vid, pid, device.serial_number,
            device.product, device.description,
        )


def filter_serial_number(
    candidates: list[Device], serial_number: Optional[str]
) -> list[Device]:
    if not serial_number:
        return candidates
    exact = [item for item in candidates if item.serial_number == serial_number]
    if exact:
        return exact
    # Some bootloaders do not expose a USB serial number. Permit fallback only
    # when none of the candidate interfaces reports a serial number.
    if not any(item.serial_number for item in candidates):
        return candidates
    return []


def select_device(devices: list[Device], config: dict, expect_bootloader: bool) -> Device:
    usb = config["usb"]
    if expect_bootloader:
        fixed_port = usb.get("bootloader_port")
        if fixed_port:
            candidates = [item for item in devices if item.device == fixed_port]
        else:
            vid, pid = int(usb["boot_vid"], 0), int(usb["boot_pid"], 0)
            candidates = [item for item in devices if item.vid == vid and item.pid == pid]
    else:
        app_vid, app_pid = int(usb["app_vid"], 0), int(usb["app_pid"], 0)
        boot_vid, boot_pid = int(usb["boot_vid"], 0), int(usb["boot_pid"], 0)
        candidates = [
            item for item in devices
            if (item.vid == app_vid and item.pid == app_pid)
            or (item.vid == boot_vid and item.pid == boot_pid)
        ]

    candidates = filter_serial_number(candidates, usb.get("serial_number"))
    if not candidates:
        raise DeviceNotFound("Configured USB serial device is not present")

    if len(candidates) > 1:
        callout = [item for item in candidates if item.device.startswith("/dev/cu.")]
        if len(callout) == 1:
            candidates = callout

    if len(candidates) > 1:
        detail = ", ".join(f"{item.device} ({item.vid:04x}:{item.pid:04x})" for item in candidates)
        raise AmbiguousDevice(f"Multiple matching USB devices: {detail}")
    return candidates[0]


def wait_for_device(
    config: dict, expect_bootloader: bool, timeout: float, poll_interval: float
) -> Device:
    deadline = time.monotonic() + timeout
    last_error: Exception = DeviceNotFound("not checked yet")
    while True:
        devices = enumerate_devices()
        try:
            return select_device(devices, config, expect_bootloader)
        except (DeviceNotFound, AmbiguousDevice) as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            log_usb_inventory(devices, str(last_error))
            raise DeviceNotFound(str(last_error))
        time.sleep(poll_interval)


class Redirector:
    """Bridge one TCP client session to one physical serial connection."""

    def __init__(self, serial_port: serial.Serial, client_socket: socket.socket, debug: bool = False):
        self.serial = serial_port
        self.socket = client_socket
        self.alive = False
        self.write_lock = threading.Lock()
        self.rfc2217 = serial.rfc2217.PortManager(
            self.serial, self, logger=LOG if debug else None
        )
        self.reader_thread: Optional[threading.Thread] = None
        self.poll_thread: Optional[threading.Thread] = None

    def write(self, data: bytes) -> None:
        with self.write_lock:
            self.socket.sendall(data)

    def serial_reader(self) -> None:
        while self.alive:
            try:
                data = self.serial.read(self.serial.in_waiting or 1)
                if data:
                    self.write(b"".join(self.rfc2217.escape(data)))
            except (OSError, serial.SerialException, socket.error) as exc:
                LOG.debug("serial reader stopped: %s", exc)
                break
        self.alive = False

    def modem_poller(self) -> None:
        while self.alive:
            time.sleep(1.0)
            if not self.alive:
                break
            try:
                self.rfc2217.check_modem_lines()
            except (OSError, serial.SerialException, socket.error):
                self.alive = False
                break

    def run(self) -> None:
        self.alive = True
        self.reader_thread = threading.Thread(target=self.serial_reader, daemon=True)
        self.poll_thread = threading.Thread(target=self.modem_poller, daemon=True)
        self.reader_thread.start()
        self.poll_thread.start()
        try:
            while self.alive:
                try:
                    data = self.socket.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                outgoing = b"".join(self.rfc2217.filter(data))
                if outgoing:
                    self.serial.write(outgoing)
        except (OSError, serial.SerialException) as exc:
            LOG.info("RFC2217 session ended: %s", exc)
        finally:
            self.stop()

    def stop(self) -> None:
        self.alive = False
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        for thread in (self.reader_thread, self.poll_thread):
            if thread and thread.is_alive():
                thread.join(timeout=2.0)


def open_physical_port(device: Device, baudrate: int) -> serial.Serial:
    port = serial.Serial(
        port=None,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=2.0,
    )
    port.dtr = False
    port.rts = False
    port.port = device.device
    port.open()
    return port


def serve(config: dict, stop_event: threading.Event, verbose: int) -> None:
    connection, agent = config["connection"], config["agent"]
    bind = agent.get("bind", "127.0.0.1")
    tcp_port = int(agent.get("port", 2217))
    default_baud = int(connection["baudrate"])
    touch_baud = int(connection["reset_baudrate"])
    timeout = float(agent.get("device_timeout", 1.5))
    poll_interval = float(agent.get("poll_interval", 0.1))

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind, tcp_port))
    listener.listen(1)
    listener.settimeout(1.0)

    LOG.info("RFC2217 listener ready on %s:%d", bind, tcp_port)
    LOG.info("Board: %s", config.get("name", "unnamed"))
    expect_bootloader = False

    try:
        while not stop_event.is_set():
            try:
                client, address = listener.accept()
            except socket.timeout:
                continue

            LOG.info("TCP client connected: %s:%d", address[0], address[1])
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(0.25)
            physical = None
            session_baud = default_baud
            try:
                device = wait_for_device(config, expect_bootloader, timeout, poll_interval)
                LOG.info("Selected USB device %s (%s)", device.device, device.description)
                physical = open_physical_port(device, default_baud)
                Redirector(physical, client, debug=verbose > 1).run()
                try:
                    session_baud = int(physical.baudrate)
                except (ValueError, TypeError, OSError):
                    session_baud = default_baud
            except (DeviceNotFound, AmbiguousDevice) as exc:
                LOG.error("USB device selection failed: %s", exc)
            except (OSError, serial.SerialException) as exc:
                LOG.exception("USB/RFC2217 session failed: %s", exc)
            finally:
                if physical is not None and physical.is_open:
                    try:
                        physical.close()
                    except (OSError, serial.SerialException) as exc:
                        LOG.warning("Error closing USB port: %s", exc)
                try:
                    client.close()
                except OSError:
                    pass

            expect_bootloader = session_baud == touch_baud
            if expect_bootloader:
                LOG.info("Client disconnected at reset baud %d; next session expects bootloader", session_baud)
            else:
                LOG.info("Client disconnected at %d baud; next session accepts app or bootloader", session_baud)
    finally:
        listener.close()
        LOG.info("RFC2217 listener stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic RFC2217 USB serial agent")
    parser.add_argument("--config", required=True, help="Board JSON configuration")
    parser.add_argument("--bind", default=None, help="Override configured bind address")
    parser.add_argument("--port", type=int, default=None, help="Override configured TCP port")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        config = load_config(Path(args.config).expanduser().resolve())
        if args.bind:
            config["agent"]["bind"] = args.bind
        if args.port:
            config["agent"]["port"] = args.port
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOG.error("Cannot load configuration: %s", exc)
        return 1

    stop_event = threading.Event()

    def request_stop(signum, frame) -> None:
        LOG.info("Received signal %s; stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        serve(config, stop_event, args.verbose)
    except OSError as exc:
        LOG.error("Server error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
