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

SPEED_PRESETS = [
    ("Slow", 1200),
    ("Normal", 2400),
    ("Fast", 6400),
    ("Very Fast", 18000),
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
        self._build_menu()

        GLib.timeout_add(500, self.refresh)
        GLib.timeout_add(2000, self.tick)

    # --------------------------------------------------------

    def _build_menu(self):
        self.menu.foreach(lambda w: self.menu.remove(w))

        dpi_root = Gtk.MenuItem(label="DPI")
        dpi_menu = Gtk.Menu()
        dpi_root.set_submenu(dpi_menu)
        for name, dpi in SPEED_PRESETS:
            item = Gtk.MenuItem(label=f"{name} ({dpi})")
            item.connect("activate", lambda _, d=dpi: self.backend.set_dpi_async(d, lambda _: None))
            dpi_menu.append(item)
        self.menu.append(dpi_root)

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

        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", Gtk.main_quit)
        self.menu.append(quit_item)

        self.menu.show_all()

    # --------------------------------------------------------

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
            if is_brightness:
                self.backend.set_led_async(val, 0, lambda _: None)   # change brightness only
            else:
                self.backend.set_led_async(0, val, lambda _: None)   # change speed only
        dialog.destroy()

    # --------------------------------------------------------

    def tick(self):
        if not self.backend.auto_connect():
            return True

        def worker():
            try:
                self.backend.read_battery()
                self.backend.read_current_dpi()
            except Exception:
                self.backend.disconnect()

        threading.Thread(target=worker, daemon=True).start()
        return True

    # --------------------------------------------------------

    def refresh(self):
        if self.backend.dev:
            if self.backend.is_sleeping():
                self.indicator.set_label("Sleeping", "")
            else:
                self.indicator.set_label(
                    f"{self.backend.dpi_value} DPI | 🔋 {self.backend.battery_percent}%",
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

