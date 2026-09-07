"""Battery gauge backed by UPower, with a sysfs fallback."""
from __future__ import annotations

import glob
import os

from gi.repository import Gio, GLib, Gtk

from ..popup import Popup, css_provider, label, separator
from ..util import rgba, rounded_rect, with_alpha
from .base import PanelItem

BW, BH = 22.0, 11.0     # battery body
NUB_W, NUB_H = 2.0, 4.5

# org.freedesktop.UPower.Device State enum
CHARGING, DISCHARGING, EMPTY, FULL, PENDING_CHARGE, PENDING_DISCHARGE = 1, 2, 3, 4, 5, 6
_STATE_TEXT = {
    CHARGING: "Charging", DISCHARGING: "On battery", EMPTY: "Empty",
    FULL: "Fully charged", PENDING_CHARGE: "Pending charge",
    PENDING_DISCHARGE: "Pending discharge",
}


def _fmt_time(seconds):
    if not seconds:
        return None
    h, m = divmod(int(seconds) // 60, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


class BatteryItem(PanelItem):
    def setup(self):
        self.percent = 0.0
        self.state = DISCHARGING
        self.time_to_empty = 0
        self.time_to_full = 0
        self.rate = 0.0
        self.present = False
        self.proxy = None
        self._sysfs = None
        self._connect_upower()
        if self.proxy is None:
            self._find_sysfs()
            self.interval_ms = 10000
            self.poll()

    # -- data sources ------------------------------------------------------
    def _connect_upower(self):
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.UPower",
                "/org/freedesktop/UPower/devices/DisplayDevice",
                "org.freedesktop.UPower.Device", None)
            if self.proxy.get_cached_property("Percentage") is None:
                self.proxy = None
                return
            self.proxy.connect("g-properties-changed", self._on_upower_changed)
            self._read_upower()
        except GLib.Error:
            self.proxy = None

    def _read_upower(self):
        def prop(name, default=0):
            val = self.proxy.get_cached_property(name)
            return val.unpack() if val is not None else default

        self.percent = float(prop("Percentage")) / 100.0
        self.state = int(prop("State", DISCHARGING))
        self.time_to_empty = int(prop("TimeToEmpty"))
        self.time_to_full = int(prop("TimeToFull"))
        self.rate = float(prop("EnergyRate"))
        self.present = bool(prop("IsPresent", True)) and int(prop("Type", 2)) == 2
        self.redraw()

    def _on_upower_changed(self, *_a):
        self._read_upower()

    def _find_sysfs(self):
        for path in sorted(glob.glob("/sys/class/power_supply/BAT*")):
            self._sysfs = path
            self.present = True
            return

    def poll(self):
        if self.proxy is not None or not self._sysfs:
            return
        try:
            with open(os.path.join(self._sysfs, "capacity"), encoding="ascii") as fh:
                self.percent = int(fh.read().strip()) / 100.0
            with open(os.path.join(self._sysfs, "status"), encoding="ascii") as fh:
                status = fh.read().strip().lower()
            self.state = {"charging": CHARGING, "full": FULL}.get(status, DISCHARGING)
        except (OSError, ValueError):
            self.present = False
        self.redraw()

    # -- appearance --------------------------------------------------------
    @property
    def visible(self):
        return self.present

    def measure(self, height):
        return BW + NUB_W + self.dock.text_width("100%", 8.5) + 18

    def _colour(self):
        if self.state in (CHARGING, PENDING_CHARGE):
            return self.theme["ok"]
        if self.state == FULL:
            return self.theme["fg_dim"]
        if self.percent <= 0.10:
            return self.theme["crit"]
        if self.percent <= 0.25:
            return self.theme["warn"]
        return self.theme["fg"]

    def _draw_bolt(self, cr, x, y, h):
        """Small lightning glyph, punched out of the fill for contrast."""
        s = h / 9.0
        cr.save()
        cr.translate(x, y)
        cr.move_to(5.2 * s, 0)
        cr.line_to(1.6 * s, 5.0 * s)
        cr.line_to(3.9 * s, 5.0 * s)
        cr.line_to(2.9 * s, 9.0 * s)
        cr.line_to(6.6 * s, 3.8 * s)
        cr.line_to(4.2 * s, 3.8 * s)
        cr.close_path()
        cr.restore()

    def draw(self, cr, w, h):
        self.draw_plate(cr, w, h)
        colour = self._colour()
        text = f"{self.percent*100:.0f}%"
        tw = self.dock.text_width(text, 8.5)
        total = BW + NUB_W + 6 + tw
        x = (w - total) / 2
        y = (h - BH) / 2

        # Body outline and the positive-terminal nub.
        rgba(cr, with_alpha(colour, 0.42))
        cr.set_line_width(1.2)
        rounded_rect(cr, x + 0.6, y + 0.6, BW - 1.2, BH - 1.2, 3.2)
        cr.stroke()
        rgba(cr, with_alpha(colour, 0.42))
        rounded_rect(cr, x + BW + 0.5, y + (BH - NUB_H) / 2, NUB_W, NUB_H, 1.0)
        cr.fill()

        inner_w = (BW - 4.4) * max(0.0, min(1.0, self.percent))
        if inner_w > 0.5:
            rgba(cr, colour)
            rounded_rect(cr, x + 2.2, y + 2.2, max(1.6, inner_w), BH - 4.4, 1.8)
            cr.fill()

        if self.state in (CHARGING, PENDING_CHARGE):
            # Erase the bolt out of the fill, then outline it, so it reads at
            # any charge level.
            cr.save()
            cr.set_operator(0)  # CLEAR
            self._draw_bolt(cr, x + BW / 2 - 4.1, y + 1.0, BH - 2.0)
            cr.set_line_width(2.4)
            cr.stroke_preserve()
            cr.fill()
            cr.restore()
            rgba(cr, colour)
            self._draw_bolt(cr, x + BW / 2 - 4.1, y + 1.0, BH - 2.0)
            cr.fill()

        lay = self.dock.pango_layout(text, 8.5, bold=True)
        sz = lay.get_pixel_size()
        rgba(cr, self.theme["fg"] if self.percent > 0.25 else colour)
        cr.move_to(x + BW + NUB_W + 6, (h - sz.height) / 2)
        self.dock.show_layout(cr, lay)

    def tooltip(self):
        parts = [f"{self.percent*100:.0f}%", _STATE_TEXT.get(self.state, "")]
        remain = _fmt_time(self.time_to_full if self.state == CHARGING
                           else self.time_to_empty)
        if remain:
            parts.append(f"{remain} {'until full' if self.state == CHARGING else 'left'}")
        return "  ·  ".join(p for p in parts if p)

    # -- popup -------------------------------------------------------------
    def on_click(self, button, x, y, event):
        if button != 1:
            return False
        if self.popup:
            self.close_popup()
            return True
        self.open_popup(self._build_popup())
        return True

    def _build_popup(self):
        pop = Popup(self.dock, padding=13)
        pop.content.set_size_request(230, -1)
        Gtk.StyleContext.add_provider_for_screen(
            pop.get_screen(), css_provider(self.theme),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        pop.content.get_style_context().add_class("td-popup")

        head = Gtk.Box(spacing=8)
        head.pack_start(label(f"{self.percent*100:.0f}%", "td-title"), False, False, 0)
        head.pack_end(label(_STATE_TEXT.get(self.state, ""), "td-dim", xalign=1.0),
                      False, False, 0)
        pop.content.pack_start(head, False, False, 0)

        bar = Gtk.DrawingArea()
        bar.set_size_request(-1, 7)
        colour = self._colour()

        def draw(widget, cr):
            aw = widget.get_allocated_width()
            rgba(cr, with_alpha(self.theme["fg"], 0.12))
            rounded_rect(cr, 0, 0, aw, 7, 3.5)
            cr.fill()
            rgba(cr, colour)
            rounded_rect(cr, 0, 0, max(7, aw * self.percent), 7, 3.5)
            cr.fill()
            return False

        bar.connect("draw", draw)
        pop.content.pack_start(bar, False, False, 2)
        pop.content.pack_start(separator(), False, False, 4)

        remain = _fmt_time(self.time_to_full if self.state == CHARGING
                           else self.time_to_empty)
        if remain:
            suffix = "until full" if self.state == CHARGING else "remaining"
            pop.content.pack_start(label(f"{remain} {suffix}"), False, False, 0)
        if self.rate:
            pop.content.pack_start(
                label(f"Drawing {self.rate:.1f} W", "td-dim"), False, False, 0)

        btn = Gtk.Button(label="Power Manager settings")
        btn.get_style_context().add_class("td-btn")

        def open_settings(_b):
            pop.dismiss()
            try:
                GLib.spawn_async(["/usr/bin/env", "xfce4-power-manager-settings"],
                                 flags=GLib.SpawnFlags.SEARCH_PATH)
            except GLib.Error:
                pass

        btn.connect("clicked", open_settings)
        pop.content.pack_start(btn, False, False, 5)
        pop.open_at(self.dock.item_center_root(self))
        return pop
