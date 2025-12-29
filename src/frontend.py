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

LED_MODES = [
    ("Mode 1", 1),
    ("Mode 2 (Custom Color)", 2),
    ("Mode 3", 3),
    ("Mode 4", 4),
    ("Mode 5", 5),
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

        self._updating_menu = False

        self.indicator = AppIndicator.Indicator.new(
            APP_ID, ensure_icon(), AppIndicator.IndicatorCategory.HARDWARE
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)

        # Keep references to items we need to update
        self.polling_radio_items = {}   # hz -> Gtk.RadioMenuItem
        self.led_mode_radio_items = {}  # mode -> Gtk.RadioMenuItem
        self.item_led_custom_color = None

        self.chk_ripple = None
        self.chk_angle = None
        self.chk_motion = None

        self.item_macro_bound = None

        self._build_menu()

        GLib.timeout_add(500, self.refresh)
        GLib.timeout_add(2000, self.tick)

    # --------------------------------------------------------

    def _build_menu(self):
        self.menu.foreach(lambda w: self.menu.remove(w))

        # -------------------------
        # DPI
        # -------------------------
        dpi_root = Gtk.MenuItem(label="DPI")
        dpi_menu = Gtk.Menu()
        dpi_root.set_submenu(dpi_menu)
        for name, dpi in DPI_PRESETS:
            item = Gtk.MenuItem(label=f"{name} ({dpi})")
            item.connect("activate", lambda _, d=dpi: self.backend.set_dpi_async(d, lambda *_: None))
            dpi_menu.append(item)
        self.menu.append(dpi_root)

        # -------------------------
        # Performance (Polling Rate)
        # -------------------------
        perf_root = Gtk.MenuItem(label="Performance")
        perf_menu = Gtk.Menu()
        perf_root.set_submenu(perf_menu)

        polling_root = Gtk.MenuItem(label="Polling Rate")
        polling_menu = Gtk.Menu()
        polling_root.set_submenu(polling_menu)

        group = None
        first = None

        for label, hz in POLLING_PRESETS:
            if first is None:
                it = Gtk.RadioMenuItem.new_with_label(None, label)
                first = it
            else:
                it = Gtk.RadioMenuItem.new_with_label(first.get_group(), label)

            it.connect("toggled", self._on_polling_toggled, hz)
            polling_menu.append(it)
            self.polling_radio_items[hz] = it

        perf_menu.append(polling_root)
        self.menu.append(perf_root)

        # -------------------------
        # Toggles
        # -------------------------
        togg_root = Gtk.MenuItem(label="Toggles")
        togg_menu = Gtk.Menu()
        togg_root.set_submenu(togg_menu)

        self.chk_ripple = Gtk.CheckMenuItem(label="Ripple Control")
        self.chk_ripple.connect("toggled", self._on_toggle, "ripple")
        togg_menu.append(self.chk_ripple)

        self.chk_angle = Gtk.CheckMenuItem(label="Angle Snap")
        self.chk_angle.connect("toggled", self._on_toggle, "angle")
        togg_menu.append(self.chk_angle)

        self.chk_motion = Gtk.CheckMenuItem(label="Motion Sync")
        self.chk_motion.connect("toggled", self._on_toggle, "motion")
        togg_menu.append(self.chk_motion)

        self.menu.append(togg_root)

        # -------------------------
        # LED
        # -------------------------
        led_root = Gtk.MenuItem(label="LED")
        led_menu = Gtk.Menu()
        led_root.set_submenu(led_menu)

        # Mode radios
        mode_root = Gtk.MenuItem(label="Mode")
        mode_menu = Gtk.Menu()
        mode_root.set_submenu(mode_menu)

        group = None
        first = None

        for label, mode in LED_MODES:
            if first is None:
                it = Gtk.RadioMenuItem.new_with_label(None, label)
                first = it
            else:
                it = Gtk.RadioMenuItem.new_with_label(first.get_group(), label)

            it.connect("toggled", self._on_led_mode_toggled, mode)
            mode_menu.append(it)
            self.led_mode_radio_items[mode] = it

        led_menu.append(mode_root)

        # Custom color (only active on mode 2)
        self.item_led_custom_color = Gtk.MenuItem(label="Custom RGB Color…")
        self.item_led_custom_color.connect("activate", lambda *_: self._led_color_dialog())
        led_menu.append(self.item_led_custom_color)

        led_menu.append(Gtk.SeparatorMenuItem())

        bright = Gtk.MenuItem(label="Brightness…")
        bright.connect("activate", lambda *_: self._led_slider_dialog("Brightness", is_brightness=True))
        led_menu.append(bright)

        speed = Gtk.MenuItem(label="Speed…")
        speed.connect("activate", lambda *_: self._led_slider_dialog("Speed", is_brightness=False))
        led_menu.append(speed)

        self.menu.append(led_root)

        # -------------------------
        # Macros (Button 4 dedicated)
        # -------------------------
        macro_root = Gtk.MenuItem(label="Macros")
        macro_menu = Gtk.Menu()
        macro_root.set_submenu(macro_menu)

        program = Gtk.MenuItem(label="Program Button 4 Macro…")
        program.connect("activate", lambda *_: self._macro_program_dialog())
        macro_menu.append(program)

        self.item_macro_bound = Gtk.CheckMenuItem(label="Button 4 Bound To Macro")
        self.item_macro_bound.connect("toggled", self._on_macro_bound_toggled)
        macro_menu.append(self.item_macro_bound)

        disable = Gtk.MenuItem(label="Unbind Button 4 Macro")
        disable.connect("activate", lambda *_: self.backend.unbind_btn4_macro_async(lambda *_: None))
        macro_menu.append(disable)

        macro_menu.append(Gtk.SeparatorMenuItem())

        showinfo = Gtk.MenuItem(label="Read Button 4 Macro Info")
        showinfo.connect("activate", lambda *_: self._macro_read_info())
        macro_menu.append(showinfo)

        self.menu.append(macro_root)

        # -------------------------
        # Quit
        # -------------------------
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", Gtk.main_quit)
        self.menu.append(quit_item)

        self.menu.show_all()

    # --------------------------------------------------------
    # Callbacks
    # --------------------------------------------------------

    def _on_polling_toggled(self, item: Gtk.RadioMenuItem, hz: int):
        if self._updating_menu:
            return
        if item.get_active():
            self.backend.set_polling_rate_async(hz, lambda *_: None)

    def _on_toggle(self, item: Gtk.CheckMenuItem, which: str):
        if self._updating_menu:
            return
        self.backend.set_toggle_async(which, item.get_active(), lambda *_: None)

    def _on_led_mode_toggled(self, item: Gtk.RadioMenuItem, mode: int):
        if self._updating_menu:
            return
        if item.get_active():
            # If user selects a mode, apply it immediately.
            # For mode 2, keep current RGB; for others ignore RGB.
            rgb = None
            if mode == 2:
                rgb = (self.backend.led_r, self.backend.led_g, self.backend.led_b)
            self.backend.set_led_mode_color_async(mode, rgb, lambda *_: None)

    def _on_macro_bound_toggled(self, item: Gtk.CheckMenuItem):
        if self._updating_menu:
            return
        if item.get_active():
            self.backend.bind_btn4_to_macro_async(lambda *_: None)
        else:
            self.backend.unbind_btn4_macro_async(lambda *_: None)

    # --------------------------------------------------------
    # Dialogs
    # --------------------------------------------------------

    def _led_slider_dialog(self, title: str, is_brightness: bool):
        dialog = Gtk.Dialog(title=title, flags=Gtk.DialogFlags.MODAL)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_default_size(320, -1)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 10, 1)
        scale.set_digits(0)
        scale.set_hexpand(True)

        # preload
        scale.set_value(self.backend.led_brightness if is_brightness else self.backend.led_speed)

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.add(Gtk.Label(label=f"Set LED {title} (1–10):"))
        box.add(scale)

        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            val = int(scale.get_value())
            if is_brightness:
                self.backend.set_led_brightness_speed_async(val, 0, lambda *_: None)
            else:
                self.backend.set_led_brightness_speed_async(0, val, lambda *_: None)
        dialog.destroy()

    def _led_color_dialog(self):
        # Only meaningful in mode 2
        if self.backend.led_mode != 2:
            return

        dialog = Gtk.ColorChooserDialog(title="Select Custom RGB", parent=None)
        dialog.set_rgba(self._rgb_to_rgba(self.backend.led_r, self.backend.led_g, self.backend.led_b))

        if dialog.run() == Gtk.ResponseType.OK:
            rgba = dialog.get_rgba()
            r = int(max(0, min(255, round(rgba.red * 255))))
            g = int(max(0, min(255, round(rgba.green * 255))))
            b = int(max(0, min(255, round(rgba.blue * 255))))
            self.backend.set_led_mode_color_async(2, (r, g, b), lambda *_: None)

        dialog.destroy()

    def _macro_program_dialog(self):
        dialog = Gtk.Dialog(
            title="Macro Editor (Button 4)",
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Save to Mouse", Gtk.ResponseType.OK
        )
        dialog.set_default_size(420, 300)

        box = dialog.get_content_area()
        box.set_spacing(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        # -------------------------
        # Macro text input
        # -------------------------
        box.add(Gtk.Label(label="Macro Text (typed exactly):"))

        textview = Gtk.TextView()
        textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        textview.set_monospace(True)

        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(textview)

        box.add(scroll)

        # -------------------------
        # Timing controls
        # -------------------------
        timing_grid = Gtk.Grid(column_spacing=10, row_spacing=6)

        lbl_press = Gtk.Label(label="Press Delay (ms):", halign=Gtk.Align.START)
        spin_press = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(20, 0, 5000, 1, 10, 0)
        )

        lbl_inter = Gtk.Label(label="Inter-key Delay (ms):", halign=Gtk.Align.START)
        spin_inter = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(30, 0, 5000, 1, 10, 0)
        )

        timing_grid.attach(lbl_press, 0, 0, 1, 1)
        timing_grid.attach(spin_press, 1, 0, 1, 1)
        timing_grid.attach(lbl_inter, 0, 1, 1, 1)
        timing_grid.attach(spin_inter, 1, 1, 1, 1)

        box.add(timing_grid)

        # -------------------------
        # Status / limits
        # -------------------------
        lbl_status = Gtk.Label(label="0 characters (0 / 70 events)")
        lbl_status.set_xalign(0.0)
        box.add(lbl_status)

        def update_status(*_):
            buf = textview.get_buffer()
            start, end = buf.get_bounds()
            text = buf.get_text(start, end, True)
            char_count = len(text)
            event_count = min(char_count * 2, 70)
            lbl_status.set_text(
                f"{char_count} characters ({event_count} / 70 events)"
            )

        textview.get_buffer().connect("changed", update_status)
        update_status()

        dialog.show_all()

        # -------------------------
        # Handle Save
        # -------------------------
        if dialog.run() == Gtk.ResponseType.OK:
            buf = textview.get_buffer()
            start, end = buf.get_bounds()
            text = buf.get_text(start, end, True)

            press_delay = int(spin_press.get_value())
            inter_delay = int(spin_inter.get_value())

            if not text:
                dialog.destroy()
                return

            def done(ok: bool):
                n = Notify.Notification.new(
                    "Macro",
                    "Macro programmed successfully"
                    if ok else "Failed to program macro",
                    None,
                )
                n.show()
                
            if not self.backend.auto_connect():
                Notify.Notification.new(
                    "Macro",
                    "Mouse not connected",
                    None
                ).show()
                dialog.destroy()
                return

            # Call backend
            self.backend.program_btn4_macro_string_async(
                text=text,
                press_delay_ms=press_delay,
                inter_key_delay_ms=inter_delay,
                on_done=done,
            )

        dialog.destroy()


    def _macro_read_info(self):
        if not self.backend.auto_connect():
            return

        def worker():
            ok = False
            try:
                ok = self.backend.read_btn4_macro_header()
            except Exception:
                self.backend.disconnect()

            def notify():
                if ok:
                    msg = f"Name: {self.backend.btn4_macro_name}\n" \
                          f"Events: {self.backend.btn4_macro_count}\n" \
                          f"Checksum OK: {self.backend.btn4_macro_checksum_ok}"
                else:
                    msg = "Failed to read macro header."
                n = Notify.Notification.new("Button 4 Macro Info", msg, None)
                n.show()
                return False

            GLib.idle_add(notify)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------

    def _rgb_to_rgba(self, r: int, g: int, b: int):
        rgba = Gtk.gdk.RGBA()
        rgba.red = max(0.0, min(1.0, r / 255.0))
        rgba.green = max(0.0, min(1.0, g / 255.0))
        rgba.blue = max(0.0, min(1.0, b / 255.0))
        rgba.alpha = 1.0
        return rgba

    # --------------------------------------------------------
    # Polling tick / UI refresh
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
                self.backend.read_led()
                self.backend.read_btn4_binding()
            except Exception:
                self.backend.disconnect()

        threading.Thread(target=worker, daemon=True).start()
        return True

    def refresh(self):
        # Update indicator label
        if self.backend.dev:
            if self.backend.is_sleeping():
                self.indicator.set_label("Sleeping", "")
            else:
                dpi = self.backend.dpi_value
                bat = self.backend.battery_percent
                pr = self.backend.polling_hz
                extra = f" | {pr}Hz" if pr > 0 else ""
                self.indicator.set_label(f"{dpi} DPI{extra} | 🔋 {bat}%", "")
        else:
            self.indicator.set_label("Disconnected", "")

        # Update menu state without triggering callbacks
        self._updating_menu = True
        try:
            # Polling radios
            if self.backend.polling_hz in self.polling_radio_items:
                self.polling_radio_items[self.backend.polling_hz].set_active(True)

            # Toggle checks
            if self.chk_ripple:
                self.chk_ripple.set_active(bool(self.backend.ripple_control))
            if self.chk_angle:
                self.chk_angle.set_active(bool(self.backend.angle_snap))
            if self.chk_motion:
                self.chk_motion.set_active(bool(self.backend.motion_sync))

            # LED mode radios + custom color sensitivity
            if self.backend.led_mode in self.led_mode_radio_items:
                self.led_mode_radio_items[self.backend.led_mode].set_active(True)
            if self.item_led_custom_color:
                self.item_led_custom_color.set_sensitive(self.backend.led_mode == 2)

            # Macro binding state
            if self.item_macro_bound:
                self.item_macro_bound.set_active(bool(self.backend.btn4_macro_bound))

        finally:
            self._updating_menu = False

        return True


def main():
    TrayApp()
    Gtk.main()

if __name__ == "__main__":
    main()

