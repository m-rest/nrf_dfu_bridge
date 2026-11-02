#!/usr/bin/env python3
"""Generic RFC2217 USB-serial bridge configured by a board JSON file."""
from __future__ import annotations

import argparse
import json
import logging
import queue
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
            device=port.device, vid=port.vid, pid=port.pid,
            serial_number=port.serial_number, description=port.description or "",
            hwid=port.hwid or "", manufacturer=getattr(port, "manufacturer", None),
            product=getattr(port, "product", None), interface=getattr(port, "interface", None),
        ))
    return result


def log_usb_inventory(devices: list[Device], reason: str) -> None:
    LOG.warning("USB inventory (%s): %d serial port(s)", reason, len(devices))
    for device in devices:
        vid = f"{device.vid:04x}" if device.vid is not None else "----"
        pid = f"{device.pid:04x}" if device.pid is not None else "----"
        LOG.warning("  port=%r vid:pid=%s:%s serial=%r product=%r description=%r",
                    device.device, vid, pid, device.serial_number,
                    device.product, device.description)


def filter_serial_number(candidates: list[Device], serial_number: Optional[str]) -> list[Device]:
    if not serial_number:
        return candidates
    exact = [item for item in candidates if item.serial_number == serial_number]
    if exact:
        return exact
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
        candidates = [item for item in devices
                      if (item.vid == app_vid and item.pid == app_pid)
                      or (item.vid == boot_vid and item.pid == boot_pid)]
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


def wait_for_device(config: dict, expect_bootloader: bool, timeout: float,
                    poll_interval: float) -> Device:
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


def wait_for_application_device(config: dict, timeout: float,
                                poll_interval: float) -> Device:
    """Wait specifically for the normal application USB identity."""
    usb = config["usb"]
    app_vid, app_pid = int(usb["app_vid"], 0), int(usb["app_pid"], 0)
    deadline = time.monotonic() + timeout
    last_error: Exception = DeviceNotFound("application USB device not present")
    while True:
        devices = enumerate_devices()
        candidates = [item for item in devices if item.vid == app_vid and item.pid == app_pid]
        candidates = filter_serial_number(candidates, usb.get("serial_number"))
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            last_error = AmbiguousDevice("Multiple matching application USB devices: "
                                         + ", ".join(item.device for item in candidates))
        else:
            last_error = DeviceNotFound("application USB device is not present")
        if time.monotonic() >= deadline:
            log_usb_inventory(devices, str(last_error))
            raise DeviceNotFound(str(last_error))
        time.sleep(poll_interval)


class Redirector:
    """Bridge one TCP client session to one physical serial connection."""
    def __init__(self, serial_port: serial.Serial, client_socket: socket.socket,
                 debug: bool = False, upload_progress: Optional[dict] = None):
        self.serial = serial_port
        self.socket = client_socket
        self.upload_progress = upload_progress
        self.alive = False
        self.write_lock = threading.Lock()
        self.rfc2217 = serial.rfc2217.PortManager(self.serial, self, logger=LOG if debug else None)
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
                    written = self.serial.write(outgoing)
                    if self.upload_progress is not None and written:
                        with self.upload_progress["lock"]:
                            self.upload_progress["bytes"] += written
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
    port = serial.Serial(port=None, baudrate=baudrate, bytesize=serial.EIGHTBITS,
                         parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                         timeout=0.05, write_timeout=2.0)
    port.dtr = False
    port.rts = False
    port.port = device.device
    port.open()
    return port


class MonitorSocketConnection:
    """Adapter used by pyserial's RFC2217 PortManager."""
    def __init__(self, client_socket: socket.socket):
        self.socket = client_socket
        self.write_lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self.write_lock:
            self.socket.sendall(data)


class MonitorSerialProxy:
    """Serial facade retaining RFC2217 settings while the USB port is closed."""
    SETTINGS = {"baudrate", "bytesize", "parity", "stopbits", "timeout",
                "write_timeout", "xonxoff", "rtscts", "dsrdtr", "rts", "dtr",
                "break_condition"}

    def __init__(self, physical: serial.Serial):
        object.__setattr__(self, "_physical", physical)
        object.__setattr__(self, "_lock", threading.RLock())
        object.__setattr__(self, "_settings", {name: getattr(physical, name)
            for name in ("baudrate", "bytesize", "parity", "stopbits", "timeout",
                         "write_timeout", "xonxoff", "rtscts", "dsrdtr")})
        self._settings.update(rts=False, dtr=False, break_condition=False)

    def __getattr__(self, name):
        if name in self.SETTINGS:
            with self._lock:
                return getattr(self._physical, name) if self._physical.is_open else self._settings[name]
        if name == "is_open":
            return self._physical.is_open
        if name in ("cts", "dsr", "ri", "cd", "in_waiting", "out_waiting"):
            with self._lock:
                if not self._physical.is_open:
                    return 0 if name.endswith("waiting") else False
                return getattr(self._physical, name)
        if name in ("reset_input_buffer", "reset_output_buffer"):
            def purge():
                with self._lock:
                    if self._physical.is_open:
                        return getattr(self._physical, name)()
                    LOG.debug("Ignoring %s while monitor USB is closed", name)
            return purge
        if name == "send_break":
            def send_break(duration=0.25):
                with self._lock:
                    if self._physical.is_open:
                        return self._physical.send_break(duration)
                    LOG.debug("Ignoring send_break while monitor USB is closed")
            return send_break
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in self.SETTINGS:
            with self._lock:
                self._settings[name] = value
                if self._physical.is_open:
                    setattr(self._physical, name, value)
            return
        object.__setattr__(self, name, value)

    def apply_settings(self):
        with self._lock:
            for name, value in self._settings.items():
                setattr(self._physical, name, value)

    def open_usb(self, device: Device):
        with self._lock:
            if not self._physical.is_open:
                self._physical.port = device.device
                self._physical.open()
                self.apply_settings()

    def close_usb(self):
        with self._lock:
            if self._physical.is_open:
                self._physical.close()

    def read_usb(self):
        with self._lock:
            if not self._physical.is_open:
                return b""
            return self._physical.read(self._physical.in_waiting or 1)

    def write_usb(self, data: bytes):
        with self._lock:
            if not self._physical.is_open:
                return 0
            return self._physical.write(data)


def monitor_server(config: dict, bind: str, tcp_port: int,
                   stop_event: threading.Event, dfu_active: threading.Event,
                   touch_detected: threading.Event, upload_active: threading.Event,
                   monitor_paused: threading.Event, upload_progress: dict,
                   timeout: float, poll_interval: float) -> None:
    """RFC2217 socket processing and physical USB monitoring use separate threads."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind, tcp_port))
    listener.listen(1)
    listener.settimeout(1.0)
    LOG.info("RFC2217 serial monitor listener ready on %s:%d", bind, tcp_port)
    try:
        while not stop_event.is_set():
            try:
                client, address = listener.accept()
            except socket.timeout:
                continue
            LOG.info("Serial monitor connected: %s:%d", address[0], address[1])
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(0.025)
            connection = MonitorSocketConnection(client)
            physical = serial.Serial(port=None, baudrate=int(config["connection"]["baudrate"]),
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=0.05, write_timeout=2.0)
            physical.dtr = False
            physical.rts = False
            proxy = MonitorSerialProxy(physical)
            rfc = serial.rfc2217.PortManager(
                proxy, connection, logger=LOG if LOG.isEnabledFor(logging.DEBUG) else None)

            usb_to_tcp: queue.Queue[bytes] = queue.Queue()
            tcp_to_usb: queue.Queue[bytes] = queue.Queue()
            usb_error: queue.Queue[Exception] = queue.Queue(maxsize=1)
            usb_stop = threading.Event()

            def usb_worker():
                """Sole owner of the monitor USB handle."""
                next_scan = 0.0
                startup_until = 0.0
                try:
                    while not stop_event.is_set() and not usb_stop.is_set():
                        if dfu_active.is_set():
                            proxy.close_usb()
                            monitor_paused.set()
                            startup_until = 0.0
                            time.sleep(0.02)
                            continue
                        if not physical.is_open:
                            monitor_paused.set()
                            now = time.monotonic()
                            if now < next_scan:
                                time.sleep(min(0.05, next_scan - now))
                                continue
                            try:
                                device = wait_for_application_device(
                                    config, min(timeout, 0.5), poll_interval)
                                delay = max(0.0, float(config["agent"].get(
                                    "monitor_startup_delay", 2.0)))
                                if startup_until == 0.0:
                                    startup_until = time.monotonic() + delay
                                while time.monotonic() < startup_until:
                                    if stop_event.is_set() or usb_stop.is_set() or dfu_active.is_set():
                                        break
                                    time.sleep(0.02)
                                if stop_event.is_set() or usb_stop.is_set() or dfu_active.is_set():
                                    continue
                                proxy.open_usb(device)
                                monitor_paused.clear()
                                LOG.info("Monitor USB opened: port=%s baud=%s",
                                         physical.port, physical.baudrate)
                                startup_until = 0.0
                            except (DeviceNotFound, AmbiguousDevice, OSError,
                                    serial.SerialException) as exc:
                                LOG.debug("Monitor waiting for application USB: %s", exc)
                                next_scan = time.monotonic() + poll_interval
                                continue
                        try:
                            data = proxy.read_usb()
                            if data:
                                usb_to_tcp.put(data)
                            while True:
                                try:
                                    payload = tcp_to_usb.get_nowait()
                                except queue.Empty:
                                    break
                                if not dfu_active.is_set():
                                    proxy.write_usb(payload)
                        except (OSError, serial.SerialException) as exc:
                            LOG.warning("Monitor USB worker failed: %s", exc)
                            try:
                                usb_error.put_nowait(exc)
                            except queue.Full:
                                pass
                            return
                finally:
                    try:
                        proxy.close_usb()
                    except (OSError, serial.SerialException):
                        pass
                    monitor_paused.set()

            worker = threading.Thread(target=usb_worker, name="monitor-usb", daemon=True)
            worker.start()
            announced_dfu = False
            upload_message_started = False
            next_progress_byte = 1024
            upload_start_bytes = 0
            last_modem_poll = time.monotonic()
            try:
                while not stop_event.is_set():
                    if not usb_error.empty():
                        raise usb_error.get_nowait()
                    while True:
                        try:
                            serial_data = usb_to_tcp.get_nowait()
                        except queue.Empty:
                            break
                        client.sendall(b"".join(rfc.escape(serial_data)))

                    if dfu_active.is_set():
                        if touch_detected.is_set() and not announced_dfu:
                            connection.write(b"".join(rfc.escape(
                                b"\r\n\r\n-----------\r\n"
                                b"[BRIDGE] 1200-baud touch detected; waiting for DFU upload\r\n")))
                            announced_dfu = True
                        if upload_active.is_set() and not upload_message_started:
                            connection.write(b"".join(rfc.escape(b"[BRIDGE] Uploading ")))
                            upload_message_started = True
                        if upload_active.is_set() and upload_message_started:
                            with upload_progress["lock"]:
                                transferred = upload_progress["bytes"] - upload_start_bytes
                            while transferred >= next_progress_byte:
                                connection.write(b"".join(rfc.escape(b".")))
                                next_progress_byte += 1024
                    elif announced_dfu or upload_message_started:
                        if upload_message_started:
                            with upload_progress["lock"]:
                                transferred = upload_progress["bytes"] - upload_start_bytes
                            ending = b" done" if transferred > 0 else b" no data received"
                            connection.write(b"".join(rfc.escape(
                                ending + b"\r\n-----------\r\n\r\n")))
                        else:
                            connection.write(b"".join(rfc.escape(
                                b"\r\n-----------\r\n\r\n")))
                        announced_dfu = False
                        upload_message_started = False
                        next_progress_byte = 1024

                    try:
                        incoming = client.recv(4096)
                        if not incoming:
                            break
                        payload = b"".join(rfc.filter(incoming))
                        if payload and not dfu_active.is_set():
                            tcp_to_usb.put(payload)
                    except socket.timeout:
                        pass
                    if time.monotonic() - last_modem_poll >= 1.0:
                        rfc.check_modem_lines()
                        last_modem_poll = time.monotonic()
            except (OSError, serial.SerialException, ConnectionError) as exc:
                LOG.info("Serial monitor session ended: %s", exc)
            finally:
                usb_stop.set()
                worker.join(timeout=2.0)
                try:
                    client.close()
                except OSError:
                    pass
                LOG.info("RFC2217 serial monitor disconnected")
    finally:
        listener.close()
        LOG.info("Serial monitor listener stopped")


def serve(config: dict, stop_event: threading.Event, verbose: int) -> None:
    LOG.info("Board: %s", config.get("name", "unnamed"))
    connection, agent = config["connection"], config["agent"]
    bind = agent.get("bind", "127.0.0.1")
    tcp_port = int(agent["dfu_tcp_port"])
    monitor_port = int(agent["monitor_tcp_port"])
    default_baud = int(connection["baudrate"])
    touch_baud = int(connection["reset_baudrate"])
    timeout = float(agent.get("device_timeout", 1.5))
    poll_interval = float(agent.get("poll_interval", 0.1))
    if monitor_port == tcp_port:
        raise ValueError("agent.dfu_tcp_port and agent.monitor_tcp_port must differ")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind, tcp_port))
    listener.listen(1)
    listener.settimeout(1.0)
    LOG.info("RFC2217 DFU listener ready on %s:%d", bind, tcp_port)

    dfu_active = threading.Event()
    touch_detected = threading.Event()
    upload_active = threading.Event()
    monitor_paused = threading.Event()
    monitor_paused.set()
    upload_progress = {"bytes": 0, "lock": threading.Lock()}
    monitor_thread = threading.Thread(
        target=monitor_server,
        args=(config, bind, monitor_port, stop_event, dfu_active, touch_detected,
              upload_active, monitor_paused, upload_progress, timeout, poll_interval),
        name="serial-monitor", daemon=True)
    monitor_thread.start()
    expect_bootloader = False
    try:
        while not stop_event.is_set():
            try:
                client, address = listener.accept()
            except socket.timeout:
                continue
            LOG.info("DFU TCP client connected: %s:%d", address[0], address[1])
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(0.25)
            physical = None
            session_baud = default_baud
            is_bootloader_upload = expect_bootloader
            if is_bootloader_upload:
                with upload_progress["lock"]:
                    upload_progress["bytes"] = 0
                upload_active.set()
            dfu_active.set()
            if not monitor_paused.wait(timeout=max(timeout, 2.0)):
                LOG.warning("Monitor did not release USB port in time; continuing with DFU")
            try:
                device = wait_for_device(config, expect_bootloader, timeout, poll_interval)
                LOG.info("Selected USB device %s (%s)", device.device, device.description)
                physical = open_physical_port(device, default_baud)
                Redirector(physical, client, debug=verbose > 1,
                           upload_progress=upload_progress if is_bootloader_upload else None).run()
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
                if is_bootloader_upload:
                    upload_active.clear()
                if not is_bootloader_upload and session_baud == touch_baud:
                    expect_bootloader = True
                    touch_detected.set()
                    LOG.info("1200-baud touch connection closed; keeping DFU mode active "
                             "and waiting for the bootloader upload connection")
                else:
                    LOG.info("DFU upload connection closed (session baud %d); "
                             "normal serial monitoring resumes", session_baud)
                    expect_bootloader = False
                    touch_detected.clear()
                    dfu_active.clear()
    finally:
        stop_event.set()
        listener.close()
        monitor_thread.join(timeout=3.0)
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
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    try:
        config = load_config(Path(args.config).expanduser().resolve())
        if args.bind:
            config["agent"]["bind"] = args.bind
        if args.port:
            config["agent"]["dfu_tcp_port"] = args.port
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
