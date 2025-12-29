#!/usr/bin/env python3
from __future__ import annotations

import os
import glob
import time
import select
import threading
from dataclasses import dataclass
from typing import Optional, List, Dict, Callable

import gi
gi.require_version("GLib", "2.0")
gi.require_version("GObject", "2.0")
from gi.repository import GLib, GObject


# ============================================================
# Checksums / framing
# ============================================================

def checksum_0x55(data: bytes) -> int:
    return (0x55 - (sum(data) & 0xFF)) & 0xFF

def pack17(payload16: bytes) -> bytes:
    return payload16 + bytes([checksum_0x55(payload16)])


# ============================================================
# HID raw discovery
# ============================================================

@dataclass
class HidrawInfo:
    path: str
    name: str

def list_hidraw() -> List[HidrawInfo]:
    out: List[HidrawInfo] = []
    for p in sorted(glob.glob("/dev/hidraw*")):
        name = ""
        try:
            with open(f"/sys/class/hidraw/{os.path.basename(p)}/device/uevent") as f:
                for line in f:
                    if line.startswith("HID_NAME="):
                        name = line.strip().split("=", 1)[1]
        except Exception:
            pass
        out.append(HidrawInfo(p, name))
    return out


# ============================================================
# hidraw I/O
# ============================================================

class HidDevice:
    def __init__(self, path: str):
        self.path = path
        self.fd: Optional[int] = None
        self.lock = threading.Lock()

    def open(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _read_any_valid17(self, timeout: float) -> Optional[bytes]:
        if self.fd is None:
            return None
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        data = os.read(self.fd, 64)
        for i in range(len(data) - 16):
            pkt = data[i:i+17]
            if checksum_0x55(pkt[:16]) == pkt[16]:
                return pkt
        return None

    def transceive_expect(self, tx17: bytes, expect_cmd: int, timeout: float) -> Optional[bytes]:
        if self.fd is None:
            return None
        with self.lock:
            os.write(self.fd, tx17)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                pkt = self._read_any_valid17(0.05)
                if pkt and pkt[1] == expect_cmd:
                    return pkt
        return None


# ============================================================
# Commands
# ============================================================

def cmd_read_battery() -> bytes:
    return pack17(bytes([0x08, 0x04] + [0x00] * 14))

def cmd_read_flash(addr: int, count: int) -> bytes:
    p = bytearray(16)
    p[0] = 0x08
    p[1] = 0x08
    p[3] = (addr >> 8) & 0xFF
    p[4] = addr & 0xFF
    p[5] = count
    return pack17(bytes(p))

def cmd_write_0807(addr: int, data: bytes) -> bytes:
    """
    Writes command 0x07 with this format:
      p[0]=0x08, p[1]=0x07
      p[3..4]=addr
      p[5]=len(data)+1
      p[6..]=data
      next byte = checksum_0x55(data)
      final byte = checksum_0x55(payload16)
    """
    count = len(data) + 1
    p = bytearray(16)
    p[0] = 0x08
    p[1] = 0x07
    p[3] = (addr >> 8) & 0xFF
    p[4] = addr & 0xFF
    p[5] = count
    p[6:6+len(data)] = data
    p[6+len(data)] = checksum_0x55(data)
    return pack17(bytes(p))


# ============================================================
# Registers
# ============================================================

# Polling rate is a single byte at 0x0000: interval in ms
# 0x01=1000Hz, 0x02=500Hz, 0x04=250Hz, 0x08=125Hz
POLLING_RATE_ADDR = 0x0000

DPI_LEVEL_SELECT_ADDR = 0x0004
DPI_LEVEL_BASE_ADDR   = 0x000C
DPI_LEVEL_STRIDE      = 0x0004
DPI_LEVEL_COUNT       = 5

LED_CONFIG_ADDR = 0x00A0
LED_APPLY_ADDR  = 0x00A7

def dpi_to_raw(dpi: int) -> int:
    return max(1, min(255, int(round(dpi / 100))))

def polling_hz_to_ms(hz: int) -> int:
    mapping = {1000: 1, 500: 2, 250: 4, 125: 8}
    if hz in mapping:
        return mapping[hz]
    # snap to nearest supported
    candidates = [1000, 500, 250, 125]
    nearest = min(candidates, key=lambda x: abs(x - hz))
    return mapping[nearest]

def polling_ms_to_hz(ms: int) -> int:
    ms = max(1, ms)
    return int(1000 // ms)


# ============================================================
# Backend
# ============================================================

class Backend(GObject.Object):
    SLEEP_TIMEOUT = 5.0

    def __init__(self):
        super().__init__()
        self.devices: List[Dict[str, str]] = []
        self.dev: Optional[HidDevice] = None

        self.dpi_value = -1
        self.battery_percent = -1
        self.polling_hz = -1  # NEW

        self.last_rx_time = 0.0
        self._io_gate = threading.RLock()

        self.refresh_devices()

    # --------------------------------------------------------

    def refresh_devices(self):
        self.devices = [{"path": d.path, "name": d.name} for d in list_hidraw()]

    def auto_connect(self) -> bool:
        if self.dev:
            return True
        self.refresh_devices()
        for d in self.devices:
            try:
                dev = HidDevice(d["path"])
                dev.open()
                if dev.transceive_expect(cmd_read_battery(), 0x04, 0.2):
                    self.dev = dev
                    self.last_rx_time = time.monotonic()
                    return True
                dev.close()
            except Exception:
                pass
        return False

    def disconnect(self):
        if self.dev:
            self.dev.close()
        self.dev = None

    def is_sleeping(self) -> bool:
        return self.dev and (time.monotonic() - self.last_rx_time) > self.SLEEP_TIMEOUT

    # --------------------------------------------------------
    # Reads
    # --------------------------------------------------------

    def read_battery(self) -> bool:
        if not self.dev:
            return False
        rx = self.dev.transceive_expect(cmd_read_battery(), 0x04, 0.2)
        if not rx:
            return False
        self.last_rx_time = time.monotonic()
        self.battery_percent = max(0, min(100, rx[6]))
        return True

    def read_current_dpi(self) -> bool:
        level_b = self.read_flash(DPI_LEVEL_SELECT_ADDR, 1)
        if not level_b:
            return False
        level = level_b[0] & 0x7F
        slot = self.read_flash(DPI_LEVEL_BASE_ADDR + level * DPI_LEVEL_STRIDE, 3)
        if not slot:
            return False
        self.dpi_value = slot[0] * 100
        self.last_rx_time = time.monotonic()
        return True

    def read_polling_rate(self) -> bool:
        """
        Polling rate stored at 0x0000 as interval in milliseconds:
          1 -> 1000 Hz, 2 -> 500 Hz, 4 -> 250 Hz, 8 -> 125 Hz
        """
        b = self.read_flash(POLLING_RATE_ADDR, 1)
        if not b:
            return False
        interval_ms = b[0]
        self.polling_hz = polling_ms_to_hz(interval_ms)
        self.last_rx_time = time.monotonic()
        return True

    def read_flash(self, addr: int, count: int) -> Optional[bytes]:
        if not self.dev:
            return None
        rx = self.dev.transceive_expect(cmd_read_flash(addr, count), 0x08, 0.3)
        if not rx:
            return None
        return bytes(rx[6:6+count])

    # --------------------------------------------------------
    # Writes
    # --------------------------------------------------------

    def set_dpi_async(self, dpi: int, on_done: Callable[[bool], None]):
        def worker():
            try:
                if not self.dev:
                    GLib.idle_add(on_done, False)
                    return
                raw = dpi_to_raw(dpi)
                pkt = cmd_write_0807(DPI_LEVEL_BASE_ADDR, bytes([raw, raw, 0x00]))
                self.dev.transceive_expect(pkt, 0x07, 0.2)
                self.dpi_value = dpi
                self.last_rx_time = time.monotonic()
                GLib.idle_add(on_done, True)
            except Exception:
                GLib.idle_add(on_done, False)
        threading.Thread(target=worker, daemon=True).start()

    def set_led_async(self, brightness_1_10: int, speed_1_10: int, on_done: Callable[[bool], None]):
        def worker():
            try:
                if not self.dev:
                    GLib.idle_add(on_done, False)
                    return

                cfg_full = self.read_flash(LED_CONFIG_ADDR, 10)
                if not cfg_full:
                    raise IOError

                # first 6 bytes: [mode, R, G, B, speed, brightness]
                cfg = bytearray(cfg_full[:6])

                # speed is cfg[4], brightness is cfg[5]
                if speed_1_10 != 0:
                    cfg[4] = max(0, min(9, speed_1_10 - 1))
                if brightness_1_10 != 0:
                    cfg[5] = max(0, min(9, brightness_1_10 - 1))

                self.dev.transceive_expect(cmd_write_0807(LED_CONFIG_ADDR, bytes(cfg)), 0x07, 0.2)
                self.dev.transceive_expect(cmd_write_0807(LED_APPLY_ADDR, bytes([0x01])), 0x07, 0.2)

                self.last_rx_time = time.monotonic()
                GLib.idle_add(on_done, True)
            except Exception:
                GLib.idle_add(on_done, False)

        threading.Thread(target=worker, daemon=True).start()

    def set_polling_rate_async(self, hz: int, on_done: Callable[[bool], None]):
        """
        Writes polling interval (ms) to 0x0000 via cmd 0x07.
        hz snaps to {125, 250, 500, 1000}.
        """
        def worker():
            try:
                if not self.dev:
                    GLib.idle_add(on_done, False)
                    return

                interval_ms = polling_hz_to_ms(hz)
                pkt = cmd_write_0807(POLLING_RATE_ADDR, bytes([interval_ms]))
                rx = self.dev.transceive_expect(pkt, 0x07, 0.2)

                ok = rx is not None
                if ok:
                    self.polling_hz = polling_ms_to_hz(interval_ms)
                    self.last_rx_time = time.monotonic()

                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)

        threading.Thread(target=worker, daemon=True).start()

