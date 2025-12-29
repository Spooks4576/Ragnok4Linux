from __future__ import annotations

import os
import glob
import time
import select
import threading
from dataclasses import dataclass
from typing import Optional, List, Dict, Callable, Tuple

import gi
gi.require_version("GLib", "2.0")
gi.require_version("GObject", "2.0")
from gi.repository import GLib, GObject

def checksum_0x55(data: bytes) -> int:
    """0x55 - (sum(data) mod 256)"""
    return (0x55 - (sum(data) & 0xFF)) & 0xFF

def pack17(payload16: bytes) -> bytes:
    """Append packet checksum over the first 16 bytes"""
    if len(payload16) != 16:
        raise ValueError("payload16 must be exactly 16 bytes")
    return payload16 + bytes([checksum_0x55(payload16)])

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
        if len(data) < 17:
            return None

        for i in range(0, len(data) - 17 + 1):
            pkt = data[i:i+17]
            if checksum_0x55(pkt[:16]) == pkt[16]:
                return pkt
        return None

    def transceive_expect(self, tx17: bytes, expect_cmd: int, timeout: float) -> Optional[bytes]:
        """
        Send framed tx17 and wait for framed reply with pkt[1] == expect_cmd.
        """
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

def cmd_read_battery() -> bytes:
    return pack17(bytes([0x08, 0x04] + [0x00] * 14))

def cmd_read_flash(addr: int, count: int) -> bytes:
    p = bytearray(16)
    p[0] = 0x08
    p[1] = 0x08
    p[3] = (addr >> 8) & 0xFF
    p[4] = addr & 0xFF
    p[5] = count & 0xFF
    return pack17(bytes(p))

def cmd_write_0807_checked(addr: int, data: bytes) -> bytes:
    """
    Checked write style:
      count = len(data) + 1
      payload includes trailing checksum_0x55(data)
    data length must be <= 9.
    """
    if len(data) > 9:
        raise ValueError("checked write supports up to 9 bytes")
    count = len(data) + 1
    p = bytearray(16)
    p[0] = 0x08
    p[1] = 0x07
    p[3] = (addr >> 8) & 0xFF
    p[4] = addr & 0xFF
    p[5] = count & 0xFF
    p[6:6+len(data)] = data
    p[6+len(data)] = checksum_0x55(data)
    return pack17(bytes(p))

def cmd_write_0807_raw(addr: int, data: bytes) -> bytes:
    """
    Raw write style seen in your macro dumps:
      count = len(data)
      no trailing checksum inside payload
    data length must be <= 10.
    """
    if len(data) > 10:
        raise ValueError("raw write supports up to 10 bytes")
    p = bytearray(16)
    p[0] = 0x08
    p[1] = 0x07
    p[3] = (addr >> 8) & 0xFF
    p[4] = addr & 0xFF
    p[5] = len(data) & 0xFF
    p[6:6+len(data)] = data
    return pack17(bytes(p))

DPI_LEVEL_SELECT_ADDR = 0x0004
DPI_LEVEL_BASE_ADDR   = 0x000C
DPI_LEVEL_STRIDE      = 0x0004
DPI_LEVEL_COUNT       = 5

def dpi_to_raw(dpi: int) -> int:
    return max(1, min(255, int(round(dpi / 100))))

POLLING_RATE_ADDR = 0x0000
POLLING_CODE_TO_HZ = {1: 125, 2: 250, 3: 500, 4: 1000}
POLLING_HZ_TO_CODE = {v: k for k, v in POLLING_CODE_TO_HZ.items()}

MOTION_SYNC_ADDR    = 0x00AB
ANGLE_SNAP_ADDR     = 0x00AF
RIPPLE_CONTROL_ADDR = 0x00B1

LED_CONFIG_ADDR = 0x00A0  

LED_APPLY_ADDR  = 0x00A7  

BUTTON_MAP_ADDR  = 0x0070
BTN4_BIND_DATA   = bytes([0x06, 0x04, 0x01])
BTN4_UNBIND_DATA = bytes([0x06, 0x04, 0xFE])

MACRO_SLOT0_ADDR = 0x0900
MACRO_RECORD_LEN = 384

HID_KEYS: Dict[str, Tuple[int, int]] = {

    **{chr(ord('a')+i): (0x04+i, 0x00) for i in range(26)},

    "1": (0x1E, 0x00), "2": (0x1F, 0x00), "3": (0x20, 0x00), "4": (0x21, 0x00),
    "5": (0x22, 0x00), "6": (0x23, 0x00), "7": (0x24, 0x00), "8": (0x25, 0x00),
    "9": (0x26, 0x00), "0": (0x27, 0x00),

    " ":  (0x2C, 0x00),
    "\n": (0x28, 0x00),   

    "\t": (0x2B, 0x00),   

    "-": (0x2D, 0x00),
    "=": (0x2E, 0x00),
    "[": (0x2F, 0x00),
    "]": (0x30, 0x00),
    "\\": (0x31, 0x00),
    ";": (0x33, 0x00),
    "'": (0x34, 0x00),
    "`": (0x35, 0x00),
    ",": (0x36, 0x00),
    ".": (0x37, 0x00),
    "/": (0x38, 0x00),

    "!": (0x1E, 0x02),
    "@": (0x1F, 0x02),
    "#": (0x20, 0x02),
    "$": (0x21, 0x02),
    "%": (0x22, 0x02),
    "^": (0x23, 0x02),
    "&": (0x24, 0x02),
    "*": (0x25, 0x02),
    "(": (0x26, 0x02),
    ")": (0x27, 0x02),

    "_": (0x2D, 0x02),
    "+": (0x2E, 0x02),
    "{": (0x2F, 0x02),
    "}": (0x30, 0x02),
    "|": (0x31, 0x02),
    ":": (0x33, 0x02),
    "\"": (0x34, 0x02),
    "~": (0x35, 0x02),
    "<": (0x36, 0x02),
    ">": (0x37, 0x02),
    "?": (0x38, 0x02),
}

def hid_keycode_for_char(ch: str) -> Optional[Tuple[int, int]]:
    """
    Returns (keycode, modifier). Uppercase letters auto-shift.
    """
    if len(ch) != 1:
        return None

    if ch.isalpha() and ch.isupper():
        base = ch.lower()
        if base in HID_KEYS:
            key, _ = HID_KEYS[base]
            return (key, 0x02)
        return None

    return HID_KEYS.get(ch)

def build_macro_string_record(
    *,
    name: str,
    text: str,
    press_delay_ms: int,
    inter_key_delay_ms: int,
) -> bytes:
    """
    Build 384-byte macro record that types 'text' as press/release pairs.
    Firmware limit: max 70 events => ~35 chars.
    """
    press_delay_ms = max(0, min(65535, int(press_delay_ms)))
    inter_key_delay_ms = max(0, min(65535, int(inter_key_delay_ms)))

    events: List[Tuple[int, int, int, int]] = []
    for idx, ch in enumerate(text):
        kc = hid_keycode_for_char(ch)
        if kc is None:
            continue
        keycode, modifier = kc

        events.append((0x80, keycode, modifier, press_delay_ms))

        delay_after = inter_key_delay_ms if idx < (len(text) - 1) else 0
        events.append((0x40, keycode, modifier, delay_after))

        if len(events) >= 70:
            break

    buf = bytearray([0xFF] * MACRO_RECORD_LEN)

    name_b = name.encode("ascii", errors="ignore")[:29]
    buf[0] = len(name_b)
    buf[1:1+len(name_b)] = name_b

    buf[31] = len(events)

    KEY_TYPE_KEYBOARD = 0x01
    off = 32
    for flag, keycode, modifier, delay in events:
        buf[off + 0] = (flag | KEY_TYPE_KEYBOARD) & 0xFF
        buf[off + 1] = keycode & 0xFF
        buf[off + 2] = modifier & 0xFF
        buf[off + 3] = (delay >> 8) & 0xFF
        buf[off + 4] = delay & 0xFF
        off += 5

    chk_index = 32 + 5 * len(events)
    buf[chk_index] = checksum_0x55(buf[31:chk_index])

    return bytes(buf)

class Backend(GObject.Object):
    SLEEP_TIMEOUT = 5.0

    def __init__(self):
        super().__init__()
        self.devices: List[Dict[str, str]] = []
        self.dev: Optional[HidDevice] = None

        self.dpi_value = -1
        self.battery_percent = -1
        self.polling_hz = -1

        self.ripple_control = False
        self.angle_snap = False
        self.motion_sync = False

        self.led_mode = -1
        self.led_r = 0
        self.led_g = 0
        self.led_b = 0
        self.led_speed = 0          

        self.led_brightness = 0     

        self.btn4_macro_bound = False

        self.last_rx_time = 0.0

        self.refresh_devices()

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
                if dev.transceive_expect(cmd_read_battery(), 0x04, 0.25):
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
        return self.dev is not None and (time.monotonic() - self.last_rx_time) > self.SLEEP_TIMEOUT

    def _require_dev(self) -> HidDevice:
        if not self.dev:
            raise IOError("device not connected")
        return self.dev

    def read_flash(self, addr: int, count: int) -> Optional[bytes]:
        dev = self._require_dev()
        rx = dev.transceive_expect(cmd_read_flash(addr, count), 0x08, 0.40)
        if not rx:
            return None
        self.last_rx_time = time.monotonic()
        return bytes(rx[6:6+count])

    def _write_checked(self, addr: int, data: bytes, timeout: float = 0.35) -> bool:
        dev = self._require_dev()
        rx = dev.transceive_expect(cmd_write_0807_checked(addr, data), 0x07, timeout)
        if not rx:
            return False
        self.last_rx_time = time.monotonic()
        return True

    def _write_raw(self, addr: int, data: bytes, timeout: float = 0.35) -> bool:
        dev = self._require_dev()
        rx = dev.transceive_expect(cmd_write_0807_raw(addr, data), 0x07, timeout)
        if not rx:
            return False
        self.last_rx_time = time.monotonic()
        return True

    def read_battery(self) -> bool:
        dev = self._require_dev()
        rx = dev.transceive_expect(cmd_read_battery(), 0x04, 0.25)
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
        return True

    def read_polling_rate(self) -> bool:
        b = self.read_flash(POLLING_RATE_ADDR, 2)
        if not b:
            return False
        if b[1] != checksum_0x55(bytes([b[0]])):
            return False
        self.polling_hz = POLLING_CODE_TO_HZ.get(b[0], -1)
        return True

    def read_toggles(self) -> bool:
        def read_bool(addr: int) -> Optional[bool]:
            bb = self.read_flash(addr, 2)
            if not bb:
                return None
            if bb[1] != checksum_0x55(bytes([bb[0]])):
                return None
            return bb[0] != 0

        r = read_bool(RIPPLE_CONTROL_ADDR)
        a = read_bool(ANGLE_SNAP_ADDR)
        m = read_bool(MOTION_SYNC_ADDR)
        if r is None or a is None or m is None:
            return False

        self.ripple_control = r
        self.angle_snap = a
        self.motion_sync = m
        return True

    def read_led(self) -> bool:
        cfg_full = self.read_flash(LED_CONFIG_ADDR, 10)
        if not cfg_full:
            return False

        mode, r, g, b, spd, bri = cfg_full[:6]
        self.led_mode = int(mode)
        self.led_r = int(r)
        self.led_g = int(g)
        self.led_b = int(b)
        self.led_speed = int(spd) + 1
        self.led_brightness = int(bri) + 1
        return True

    def read_btn4_binding(self) -> bool:
        b = self.read_flash(BUTTON_MAP_ADDR, 4)
        if not b:
            return False
        if b[3] != checksum_0x55(b[:3]):
            return False
        self.btn4_macro_bound = (b[0] == 0x06 and b[1] == 0x04 and b[2] == 0x01)
        return True

    def set_dpi_async(self, dpi: int, on_done: Callable[[bool], None]):
        def worker():
            try:
                raw = dpi_to_raw(dpi)
                ok = self._write_checked(DPI_LEVEL_BASE_ADDR, bytes([raw, raw, 0x00]))
                if ok:
                    self.dpi_value = dpi
                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)
        threading.Thread(target=worker, daemon=True).start()

    def set_polling_rate_async(self, hz: int, on_done: Callable[[bool], None]):
        def worker():
            try:
                code = POLLING_HZ_TO_CODE.get(int(hz))
                if code is None:
                    GLib.idle_add(on_done, False)
                    return
                ok = self._write_checked(POLLING_RATE_ADDR, bytes([code]))
                if ok:
                    self.polling_hz = int(hz)
                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)
        threading.Thread(target=worker, daemon=True).start()

    def set_toggle_async(self, which: str, enabled: bool, on_done: Callable[[bool], None]):
        addr = {
            "ripple": RIPPLE_CONTROL_ADDR,
            "angle": ANGLE_SNAP_ADDR,
            "motion": MOTION_SYNC_ADDR,
        }.get(which)

        def worker():
            try:
                if addr is None:
                    GLib.idle_add(on_done, False)
                    return
                ok = self._write_checked(addr, bytes([0x01 if enabled else 0x00]))
                if ok:
                    if which == "ripple":
                        self.ripple_control = enabled
                    elif which == "angle":
                        self.angle_snap = enabled
                    elif which == "motion":
                        self.motion_sync = enabled
                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)

        threading.Thread(target=worker, daemon=True).start()

    def set_led_brightness_speed_async(self, brightness_1_10: int, speed_1_10: int, on_done: Callable[[bool], None]):
        def worker():
            try:
                cfg_full = self.read_flash(LED_CONFIG_ADDR, 10)
                if not cfg_full:
                    raise IOError("read led config failed")

                cfg = bytearray(cfg_full[:6])  

                if speed_1_10 != 0:
                    cfg[4] = max(0, min(9, int(speed_1_10) - 1))
                if brightness_1_10 != 0:
                    cfg[5] = max(0, min(9, int(brightness_1_10) - 1))

                ok1 = self._write_checked(LED_CONFIG_ADDR, bytes(cfg))
                ok2 = self._write_checked(LED_APPLY_ADDR, bytes([0x01]))
                ok = ok1 and ok2

                if ok:
                    self.led_speed = int(cfg[4]) + 1
                    self.led_brightness = int(cfg[5]) + 1

                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)

        threading.Thread(target=worker, daemon=True).start()

    def set_led_mode_color_async(self, mode_1_5: int, rgb: Optional[Tuple[int, int, int]], on_done: Callable[[bool], None]):
        """
        Always sets mode.
        If mode==2 and rgb provided, updates RGB too.
        """
        def worker():
            try:
                cfg_full = self.read_flash(LED_CONFIG_ADDR, 10)
                if not cfg_full:
                    raise IOError("read led config failed")

                cfg = bytearray(cfg_full[:6])  

                mode = max(1, min(5, int(mode_1_5)))
                cfg[0] = mode

                if mode == 2 and rgb is not None:
                    r, g, b = rgb
                    cfg[1] = max(0, min(255, int(r)))
                    cfg[2] = max(0, min(255, int(g)))
                    cfg[3] = max(0, min(255, int(b)))

                ok1 = self._write_checked(LED_CONFIG_ADDR, bytes(cfg))
                ok2 = self._write_checked(LED_APPLY_ADDR, bytes([0x01]))
                ok = ok1 and ok2

                if ok:
                    self.led_mode = mode
                    self.led_r = int(cfg[1])
                    self.led_g = int(cfg[2])
                    self.led_b = int(cfg[3])

                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)

        threading.Thread(target=worker, daemon=True).start()

    def bind_btn4_to_macro_async(self, on_done: Callable[[bool], None]):
        def worker():
            try:
                ok = self._write_checked(BUTTON_MAP_ADDR, BTN4_BIND_DATA)
                if ok:
                    self.btn4_macro_bound = True
                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)
        threading.Thread(target=worker, daemon=True).start()

    def unbind_btn4_macro_async(self, on_done: Callable[[bool], None]):
        def worker():
            try:
                ok = self._write_checked(BUTTON_MAP_ADDR, BTN4_UNBIND_DATA)
                if ok:
                    self.btn4_macro_bound = False
                GLib.idle_add(on_done, ok)
            except Exception:
                GLib.idle_add(on_done, False)
        threading.Thread(target=worker, daemon=True).start()

    def program_btn4_macro_string_async(
        self,
        *,
        text: str,
        press_delay_ms: int,
        inter_key_delay_ms: int,
        on_done: Callable[[bool], None],
    ):
        """
        Writes macro record to slot0 (0x0900) using RAW writes, then binds Button 4.
        Includes flash pacing delay to avoid dropped writes.
        """
        def worker():
            try:
                if not self.auto_connect():
                    raise IOError("device not connected")

                record = build_macro_string_record(
                    name="string_macro",
                    text=text,
                    press_delay_ms=press_delay_ms,
                    inter_key_delay_ms=inter_key_delay_ms,
                )

                addr = MACRO_SLOT0_ADDR
                i = 0
                while i < len(record):
                    chunk = record[i:i+10]
                    if not self._write_raw(addr, chunk, timeout=0.50):
                        raise IOError(f"macro write failed at 0x{addr:04X}")
                    addr += len(chunk)
                    i += len(chunk)
                    time.sleep(0.02)  

                if not self._write_checked(BUTTON_MAP_ADDR, BTN4_BIND_DATA, timeout=0.50):
                    raise IOError("bind failed")

                self.btn4_macro_bound = True
                GLib.idle_add(on_done, True)

            except Exception as e:
                print("Macro programming error:", e)
                GLib.idle_add(on_done, False)

        threading.Thread(target=worker, daemon=True).start()
