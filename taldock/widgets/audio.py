"""Volume control: hand-drawn speaker, scroll to adjust, popup mixer."""
from __future__ import annotations

import math

from gi.repository import GLib, Gtk

from ..popup import Popup, css_provider, label, separator
from ..util import ease_out_cubic, now, rgba, rounded_rect, with_alpha
from .base import PanelItem

ICON = 17.0
FLASH_SEC = 1.1     # how long the inline level bar stays after a change


class AudioItem(PanelItem):
    def setup(self):
        self.pulse = self.dock.pulse
        self.pulse.connect("changed", self._on_changed)
        self._flash_until = 0.0
        self._flash_timer = 0

    def _on_changed(self, *_a):
        self.redraw()

    def measure(self, height):
        return 30

    # -- glyph -------------------------------------------------------------
    def _speaker(self, cr, cx, cy, s, level, muted, colour):
        """Speaker cone plus up to three level arcs, all vector."""
        rgba(cr, colour)
        cr.move_to(cx - 6 * s, cy - 2.6 * s)
        cr.line_to(cx - 2.8 * s, cy - 2.6 * s)
        cr.line_to(cx + 0.6 * s, cy - 6.2 * s)
        cr.line_to(cx + 0.6 * s, cy + 6.2 * s)
        cr.line_to(cx - 2.8 * s, cy + 2.6 * s)
        cr.line_to(cx - 6 * s, cy + 2.6 * s)
        cr.close_path()
        cr.fill()

        if muted:
            rgba(cr, colour)
            cr.set_line_width(1.6 * s)
            cr.set_line_cap(1)  # ROUND
            cr.move_to(cx + 2.6 * s, cy - 3.2 * s)
            cr.line_to(cx + 7.4 * s, cy + 3.2 * s)
            cr.move_to(cx + 7.4 * s, cy - 3.2 * s)
            cr.line_to(cx + 2.6 * s, cy + 3.2 * s)
            cr.stroke()
            return

        cr.set_line_width(1.35 * s)
        cr.set_line_cap(1)
        for i, radius in enumerate((3.0, 5.4, 7.8)):
            # Each arc lights up as the level passes its third.
            lit = level > i * 0.33
            rgba(cr, colour, 1.0 if lit else 0.22)
            cr.arc(cx + 0.2 * s, cy, radius * s, -math.pi / 3.4, math.pi / 3.4)
            cr.stroke()

    def draw(self, cr, w, h):
        self.draw_plate(cr, w, h)
        muted = self.pulse.muted or not self.pulse.available
        level = self.pulse.volume
        colour = self.theme["fg_dim"] if muted else self.theme["fg"]
        showing_bar = now() < self._flash_until
        cy = h / 2 - (2.5 if showing_bar else 0)
        self._speaker(cr, w / 2, cy, ICON / 17.0, level, muted, colour)

        if showing_bar:
            # Fades out rather than vanishing, so the change reads as one gesture.
            t = (self._flash_until - now()) / FLASH_SEC
            alpha = ease_out_cubic(min(1.0, t * 3.0))
            bw = w - 12
            by = h - 7
            rgba(cr, with_alpha(self.theme["fg"], 0.15 * alpha))
            rounded_rect(cr, 6, by, bw, 3, 1.5)
            cr.fill()
            fill = self.theme["crit"] if level > 1.0 else self.theme["accent"]
            rgba(cr, with_alpha(fill, alpha))
            rounded_rect(cr, 6, by, max(3, bw * min(1.0, level)), 3, 1.5)
            cr.fill()

    def tooltip(self):
        if not self.pulse.available:
            return "No sound server"
        if self.pulse.muted:
            return "Muted"
        return f"Volume {self.pulse.volume*100:.0f}%"

    # -- input -------------------------------------------------------------
    def _flash(self):
        self._flash_until = now() + FLASH_SEC
        if not self._flash_timer:
            self._flash_timer = GLib.timeout_add(40, self._flash_tick)

    def _flash_tick(self):
        self.redraw()
        if now() >= self._flash_until:
            self._flash_timer = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def on_scroll(self, direction, event):
        step = 0.05 if not (event.state & (1 << 0)) else 0.01   # Shift = fine
        self.pulse.step_volume(step if direction > 0 else -step)
        if self.pulse.muted and direction > 0:
            self.pulse.set_mute(False)
        self._flash()
        return True

    def on_click(self, button, x, y, event):
        if button == 2:
            self.pulse.toggle_mute()
            self._flash()
            return True
        if button != 1:
            return False
        if self.popup:
            self.close_popup()
            return True
        self.open_popup(self._build_popup())
        return True

    # -- popup -------------------------------------------------------------
    def _build_popup(self):
        pop = Popup(self.dock, padding=13)
        pop.content.set_size_request(268, -1)
        Gtk.StyleContext.add_provider_for_screen(
            pop.get_screen(), css_provider(self.theme),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        pop.content.get_style_context().add_class("td-popup")

        if not self.pulse.available:
            pop.content.pack_start(label("No sound server available", "td-dim"),
                                   False, False, 0)
            pop.open_at(self.dock.item_center_root(self))
            return pop

        head = Gtk.Box(spacing=8)
        head.pack_start(label("Output", "td-title"), False, False, 0)
        pct = label(f"{self.pulse.volume*100:.0f}%", "td-dim", xalign=1.0)
        head.pack_end(pct, False, False, 0)
        pop.content.pack_start(head, False, False, 0)

        row = Gtk.Box(spacing=8)
        mute_btn = Gtk.ToggleButton()
        mute_btn.set_active(self.pulse.muted)
        mute_btn.get_style_context().add_class("td-btn")
        mute_btn.add(Gtk.Image.new_from_icon_name(
            "audio-volume-muted-symbolic", Gtk.IconSize.BUTTON))
        row.pack_start(mute_btn, False, False, 0)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_value(self.pulse.volume * 100)
        scale.set_hexpand(True)
        row.pack_start(scale, True, True, 0)
        pop.content.pack_start(row, False, False, 0)

        guard = {"busy": False}

        def on_scale(widget):
            if guard["busy"]:
                return
            value = widget.get_value() / 100.0
            self.pulse.set_volume(value)
            if self.pulse.muted and value > 0:
                self.pulse.set_mute(False)
                mute_btn.set_active(False)
            pct.set_text(f"{value*100:.0f}%")

        def on_mute(widget):
            if guard["busy"]:
                return
            self.pulse.set_mute(widget.get_active())

        scale.connect("value-changed", on_scale)
        mute_btn.connect("toggled", on_mute)

        def sync(*_a):
            # Reflect external changes (media keys, other apps) live.
            guard["busy"] = True
            scale.set_value(self.pulse.volume * 100)
            mute_btn.set_active(self.pulse.muted)
            pct.set_text(f"{self.pulse.volume*100:.0f}%")
            guard["busy"] = False

        handler = self.pulse.connect("changed", sync)
        pop.connect("destroy", lambda *_a: self.pulse.disconnect(handler))

        if len(self.pulse.sinks) > 1:
            pop.content.pack_start(separator(), False, False, 4)
            pop.content.pack_start(label("Output device", "td-title"), False, False, 0)
            current = self.pulse.sink.index if self.pulse.sink else -1
            for sink in self.pulse.sinks:
                btn = Gtk.Button()
                btn.set_relief(Gtk.ReliefStyle.NONE)
                btn.get_style_context().add_class("td-row")
                inner = Gtk.Box(spacing=8)
                inner.pack_start(label("●" if sink.index == current else "○",
                                       "td-dim"), False, False, 0)
                inner.pack_start(label(sink.description), True, True, 0)
                btn.add(inner)
                btn.connect("clicked",
                            lambda _b, s=sink: (self.pulse.set_default_sink(s.name),
                                                pop.dismiss()))
                pop.content.pack_start(btn, False, False, 0)

        pop.content.pack_start(separator(), False, False, 4)
        mic_row = Gtk.Box(spacing=8)
        mic_row.pack_start(label("Microphone"), True, True, 0)
        mic = Gtk.Switch()
        # The switch reads as "microphone is live", so it is the inverse of mute.
        mic.set_active(not self.pulse.mic_mute)
        mic.connect("notify::active",
                    lambda sw, _p: self.pulse.set_mic_mute(not sw.get_active()))
        mic_row.pack_end(mic, False, False, 0)
        pop.content.pack_start(mic_row, False, False, 0)

        settings = Gtk.Button(label="Sound settings")
        settings.get_style_context().add_class("td-btn")
        settings.connect("clicked", lambda _b: (pop.dismiss(), self._open_mixer()))
        pop.content.pack_start(settings, False, False, 0)

        pop.open_at(self.dock.item_center_root(self))
        return pop

    def _open_mixer(self):
        for cmd in ("pavucontrol", "pavucontrol-qt", "xfce4-pulseaudio-plugin"):
            try:
                GLib.spawn_async(["/usr/bin/env", cmd],
                                 flags=GLib.SpawnFlags.SEARCH_PATH)
                return
            except GLib.Error:
                continue
