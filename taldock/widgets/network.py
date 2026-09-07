"""Network status: signal arcs for wifi, a plug glyph for wired."""
from __future__ import annotations

import math

from gi.repository import GLib, Gtk

from ..popup import Popup, css_provider, label, separator
from ..util import rgba, rounded_rect, with_alpha
from .base import PanelItem


class NetworkItem(PanelItem):
    def setup(self):
        self.net = self.dock.network
        self.net.connect("changed", lambda *_a: self.redraw())

    def measure(self, height):
        return 28

    # -- glyphs ------------------------------------------------------------
    def _wifi(self, cr, cx, cy, strength, colour, enabled=True):
        """Three nested arcs plus a dot; unlit arcs stay faintly visible."""
        cr.set_line_width(1.9)
        cr.set_line_cap(1)
        bars = 0 if strength <= 0 else min(3, strength // 27 + 1)
        for i, radius in enumerate((4.2, 7.3, 10.4)):
            lit = enabled and i < bars
            rgba(cr, colour, 1.0 if lit else 0.20)
            cr.arc(cx, cy + 4.4, radius, math.radians(218), math.radians(322))
            cr.stroke()
        rgba(cr, colour, 1.0 if enabled and strength > 0 else 0.20)
        cr.arc(cx, cy + 4.2, 1.5, 0, math.tau)
        cr.fill()
        if not enabled:
            rgba(cr, colour, 0.85)
            cr.set_line_width(1.7)
            cr.move_to(cx - 7, cy - 6)
            cr.line_to(cx + 7, cy + 7)
            cr.stroke()

    def _wired(self, cr, cx, cy, colour):
        rgba(cr, colour)
        rounded_rect(cr, cx - 7, cy - 1.5, 14, 8, 2.0)
        cr.fill()
        cr.set_line_width(1.8)
        cr.set_line_cap(1)
        for dx in (-3.6, 0, 3.6):
            cr.move_to(cx + dx, cy - 1.5)
            cr.line_to(cx + dx, cy - 6.5)
        cr.stroke()

    def draw(self, cr, w, h):
        self.draw_plate(cr, w, h)
        cx, cy = w / 2, h / 2
        if self.net.kind == "wifi":
            good = self.net.strength >= 34
            colour = self.theme["fg"] if good else self.theme["warn"]
            self._wifi(cr, cx, cy - 2, self.net.strength, colour)
        elif self.net.kind == "ethernet":
            self._wired(cr, cx, cy, self.theme["fg"])
        elif not self.net.wifi_enabled:
            self._wifi(cr, cx, cy - 2, 0, self.theme["fg_dim"], enabled=False)
        else:
            self._wifi(cr, cx, cy - 2, 0, self.theme["crit"], enabled=True)

    def tooltip(self):
        if self.net.kind == "wifi":
            return f"{self.net.ssid}  ·  {self.net.strength}%  ·  {self.net.ip4}"
        if self.net.kind == "ethernet":
            return f"Wired  ·  {self.net.ip4}"
        if not self.net.wifi_enabled:
            return "Wi-Fi off"
        return "Not connected"

    # -- popup -------------------------------------------------------------
    def on_click(self, button, x, y, event):
        if button != 1:
            return False
        if self.popup:
            self.close_popup()
            return True
        self.net.request_scan()
        self.open_popup(self._build_popup())
        return True

    def _signal_bars(self, strength, active):
        area = Gtk.DrawingArea()
        area.set_size_request(17, 15)

        def draw(_w, cr):
            colour = self.theme["accent"] if active else self.theme["fg"]
            bars = 0 if strength <= 0 else min(4, strength // 25 + 1)
            for i in range(4):
                bh = 3.5 + i * 3.2
                rgba(cr, colour, 1.0 if i < bars else 0.18)
                rounded_rect(cr, i * 4.2, 15 - bh, 2.9, bh, 1.4)
                cr.fill()
            return False

        area.connect("draw", draw)
        return area

    def _build_popup(self):
        pop = Popup(self.dock, padding=13)
        pop.content.set_size_request(276, -1)
        Gtk.StyleContext.add_provider_for_screen(
            pop.get_screen(), css_provider(self.theme),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        pop.content.get_style_context().add_class("td-popup")

        if not self.net.available:
            pop.content.pack_start(label("NetworkManager not running", "td-dim"),
                                   False, False, 0)
            pop.open_at(self.dock.item_center_root(self))
            return pop

        # -- current connection
        if self.net.kind in ("wifi", "ethernet"):
            title = self.net.ssid if self.net.kind == "wifi" else "Wired connection"
            pop.content.pack_start(label(title, "td-title"), False, False, 0)
            detail = f"{self.net.ifname}   ·   {self.net.ip4}"
            if self.net.kind == "wifi":
                detail = f"{self.net.strength}%   ·   " + detail
            pop.content.pack_start(label(detail, "td-dim"), False, False, 0)
        else:
            pop.content.pack_start(label("Not connected", "td-title"), False, False, 0)

        toggle = Gtk.Switch()
        toggle.set_active(self.net.wifi_enabled)
        toggle.connect("notify::active",
                       lambda s, _p: self.net.set_wifi_enabled(s.get_active()))
        row = Gtk.Box(spacing=8)
        row.pack_start(label("Wi-Fi"), True, True, 0)
        row.pack_end(toggle, False, False, 0)
        pop.content.pack_start(separator(), False, False, 4)
        pop.content.pack_start(row, False, False, 0)

        # -- available networks
        pop.content.pack_start(separator(), False, False, 4)
        self._networks_header = label("Networks", "td-title")
        pop.content.pack_start(self._networks_header, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(215)
        scroller.set_propagate_natural_height(True)
        self._networks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        scroller.add(self._networks_box)
        pop.content.pack_start(scroller, False, False, 0)
        self._fill_networks(pop)
        # The scan requested when the popup opened takes a second or two to
        # report, so refresh the list once results are in.
        source = GLib.timeout_add_seconds(2, self._rescan, pop)
        pop.connect("destroy", lambda *_a: GLib.source_remove(source))

        btn = Gtk.Button(label="Network connections")
        btn.get_style_context().add_class("td-btn")
        btn.connect("clicked", lambda _b: (pop.dismiss(), self._open_editor()))
        pop.content.pack_start(btn, False, False, 5)
        pop.open_at(self.dock.item_center_root(self))
        return pop

    def _fill_networks(self, pop):
        for child in self._networks_box.get_children():
            self._networks_box.remove(child)
        aps = self.net.access_points() if self.net.wifi_enabled else []
        if not aps:
            self._networks_box.pack_start(
                label("Scanning…" if self.net.wifi_enabled else "Wi-Fi is off",
                      "td-dim"), False, False, 2)
        for ap in aps[:14]:
            self._networks_box.pack_start(self._ap_row(ap, pop), False, False, 0)
        self._networks_box.show_all()

    def _rescan(self, pop):
        if not pop.get_realized():
            return GLib.SOURCE_REMOVE
        self._fill_networks(pop)
        return GLib.SOURCE_REMOVE

    def _ap_row(self, ap, pop):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("td-row")
        inner = Gtk.Box(spacing=9)
        inner.pack_start(self._signal_bars(ap.strength, ap.active), False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name = label(ap.ssid, "td-title" if ap.active else None)
        text.pack_start(name, False, False, 0)
        sub = f"{ap.band}   ·   {ap.strength}%"
        if ap.active:
            sub = "Connected   ·   " + sub
        text.pack_start(label(sub, "td-dim"), False, False, 0)
        inner.pack_start(text, True, True, 0)
        if ap.secure:
            inner.pack_end(Gtk.Image.new_from_icon_name(
                "channel-secure-symbolic", Gtk.IconSize.MENU), False, False, 0)
        btn.add(inner)
        btn.connect("clicked", lambda _b: self._activate(ap, pop))
        return btn

    def _activate(self, ap, pop):
        pop.dismiss()
        if ap.active:
            return
        if not self.net.activate(ap):
            # No saved profile: hand off to the desktop's secret agent.
            self._open_editor()

    def _open_editor(self):
        for cmd in ("nm-connection-editor", "nmtui"):
            try:
                GLib.spawn_async(["/usr/bin/env", cmd],
                                 flags=GLib.SpawnFlags.SEARCH_PATH)
                return
            except GLib.Error:
                continue
