#!/usr/bin/env python3
import os
import threading
import urllib.request
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")

from gi.repository import Gtk, GLib, Notify
from gi.repository import AyatanaAppIndicator3 as AppIndicator

from backend import Backend

APP_ID = "com.spooky.ragnok.tray"
ICON_URL = "https://cdn.4fingerstudios.com/gun.png"
ICON_CACHE = "/tmp/ragnok_mouse.png"

DPI_PRESETS = [
    ("Slow", 1200),
    ("Normal", 2400),
    ("Fast", 6400),
    ("Very Fast", 18000),
]

POLLING_PRESETS = [
    ("125 Hz", 125),
    ("250 Hz", 250),
    ("500 Hz", 500),
    ("1000 Hz", 1000),
]

def ensure_icon():
    if not os.path.exists(ICON_CACHE):
        try:
            urllib.request.urlretrieve(ICON_URL, ICON_CACHE)
        except Exception:
            return "input-mouse"
    return ICON_CACHE


class TrayApp:
    def __init__(self):
        self.backend = Backend()
        Notify.init(APP_ID)

        self.indicator = AppIndicator.Indicator.new(
            APP_ID, ensure_icon(), AppIndicator.IndicatorCategory.HARDWARE
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)

        self._suppress_toggle_events = False

        # handles to check items so we can sync UI state without rebuilding menu
        self._ripple_item = None
        self._angle_item = None
        self._motion_item = None

        self._build_menu()

        GLib.timeout_add(500, self.refresh)
        GLib.timeout_add(2000, self.tick)

    # --------------------------------------------------------

    def _build_menu(self):
        self.menu.foreach(lambda w: self.menu.remove(w))

        # DPI submenu
        dpi_root = Gtk.MenuItem(label="DPI")
        dpi_menu = Gtk.Menu()
        dpi_root.set_submenu(dpi_menu)
        for name, dpi in DPI_PRESETS:
            item = Gtk.MenuItem(label=f"{name} ({dpi})")
            item.connect("activate", lambda _, d=dpi: self._set_dpi(d))
            dpi_menu.append(item)
        self.menu.append(dpi_root)

        # LED submenu
        led_root = Gtk.MenuItem(label="LED")
        led_menu = Gtk.Menu()
        led_root.set_submenu(led_menu)

        bright = Gtk.MenuItem(label="Brightness…")
        bright.connect("activate", lambda *_: self._led_dialog("Brightness", True))
        led_menu.append(bright)

        speed = Gtk.MenuItem(label="Speed…")
        speed.connect("activate", lambda *_: self._led_dialog("Speed", False))
        led_menu.append(speed)

        self.menu.append(led_root)

        # Polling submenu
        poll_root = Gtk.MenuItem(label="Polling Rate")
        poll_menu = Gtk.Menu()
        poll_root.set_submenu(poll_menu)
        for label, hz in POLLING_PRESETS:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _, h=hz: self._set_polling(h))
            poll_menu.append(item)
        self.menu.append(poll_root)

        # Feature toggles submenu
        feat_root = Gtk.MenuItem(label="Toggles")
        feat_menu = Gtk.Menu()
        feat_root.set_submenu(feat_menu)

        self._ripple_item = Gtk.CheckMenuItem(label="Ripple Control")
        self._ripple_item.connect("toggled", self._on_toggle_ripple)
        feat_menu.append(self._ripple_item)

        self._angle_item = Gtk.CheckMenuItem(label="Angle Snap")
        self._angle_item.connect("toggled", self._on_toggle_angle)
        feat_menu.append(self._angle_item)

        self._motion_item = Gtk.CheckMenuItem(label="Motion Sync")
        self._motion_item.connect("toggled", self._on_toggle_motion)
        feat_menu.append(self._motion_item)

        self.menu.append(feat_root)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", Gtk.main_quit)
        self.menu.append(quit_item)

        self.menu.show_all()

    # --------------------------------------------------------
    # Actions
    # --------------------------------------------------------

    def _set_dpi(self, dpi: int):
        if not self.backend.auto_connect():
            return
        self.backend.set_dpi_async(dpi, lambda _: None)

    def _set_polling(self, hz: int):
        if not self.backend.auto_connect():
            return
        self.backend.set_polling_rate_async(hz, lambda _: None)

    def _led_dialog(self, title, is_brightness):
        dialog = Gtk.Dialog(title=title, flags=Gtk.DialogFlags.MODAL)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_default_size(300, -1)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 10, 1)
        scale.set_value(5)
        scale.set_digits(0)
        scale.set_hexpand(True)

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.add(Gtk.Label(label=f"Set LED {title} (1–10):"))
        box.add(scale)

        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            val = int(scale.get_value())
            if not self.backend.auto_connect():
                dialog.destroy()
                return
            if is_brightness:
                self.backend.set_led_async(val, 0, lambda _: None)   # brightness only
            else:
                self.backend.set_led_async(0, val, lambda _: None)   # speed only
        dialog.destroy()

    # --------------------------------------------------------
    # Toggle handlers (with revert-on-failure)
    # --------------------------------------------------------

    def _set_check_safely(self, item: Gtk.CheckMenuItem, value: bool):
        self._suppress_toggle_events = True
        try:
            item.set_active(value)
        finally:
            self._suppress_toggle_events = False

    def _on_toggle_ripple(self, item: Gtk.CheckMenuItem):
        if self._suppress_toggle_events:
            return
        desired = item.get_active()
        if not self.backend.auto_connect():
            self._set_check_safely(item, not desired)
            return

        def done(ok: bool):
            if not ok:
                self._set_check_safely(item, not desired)

        self.backend.set_ripple_control_async(desired, done)

    def _on_toggle_angle(self, item: Gtk.CheckMenuItem):
        if self._suppress_toggle_events:
            return
        desired = item.get_active()
        if not self.backend.auto_connect():
            self._set_check_safely(item, not desired)
            return

        def done(ok: bool):
            if not ok:
                self._set_check_safely(item, not desired)

        self.backend.set_angle_snap_async(desired, done)

    def _on_toggle_motion(self, item: Gtk.CheckMenuItem):
        if self._suppress_toggle_events:
            return
        desired = item.get_active()
        if not self.backend.auto_connect():
            self._set_check_safely(item, not desired)
            return

        def done(ok: bool):
            if not ok:
                self._set_check_safely(item, not desired)

        self.backend.set_motion_sync_async(desired, done)

    # --------------------------------------------------------
    # Polling reads (tick) + UI refresh
    # --------------------------------------------------------

    def tick(self):
        if not self.backend.auto_connect():
            return True

        def worker():
            try:
                self.backend.read_battery()
                self.backend.read_current_dpi()
                self.backend.read_polling_rate()
                self.backend.read_toggles()
            except Exception:
                self.backend.disconnect()

        threading.Thread(target=worker, daemon=True).start()
        return True

    def refresh(self):
        # Sync toggle UI state to backend state (don’t fire callbacks)
        if self._ripple_item:
            self._set_check_safely(self._ripple_item, self.backend.ripple_control)
        if self._angle_item:
            self._set_check_safely(self._angle_item, self.backend.angle_snap)
        if self._motion_item:
            self._set_check_safely(self._motion_item, self.backend.motion_sync)

        if self.backend.dev:
            if self.backend.is_sleeping():
                self.indicator.set_label("Sleeping", "")
            else:
                poll = f"{self.backend.polling_hz} Hz" if self.backend.polling_hz > 0 else "?"
                self.indicator.set_label(
                    f"{self.backend.dpi_value} DPI | {poll} | 🔋 {self.backend.battery_percent}%",
                    ""
                )
        else:
            self.indicator.set_label("Disconnected", "")
        return True


def main():
    TrayApp()
    Gtk.main()

if __name__ == "__main__":
    main()

