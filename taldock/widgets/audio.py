"""Volume control: hand-drawn speaker, scroll to adjust, popup mixer."""
from __future__ import annotations

import math

from gi.repository import GLib, Gtk

from ..popup import Popup, label, separator
from ..util import (ease_out_cubic, launch_first, now, rgba, rounded_rect,
                     with_alpha)
from .base import PanelItem

ICON = 17.0
FLASH_SEC = 1.1     # how long the inline level bar stays after a change
VOLUME_STEP = 0.05
FINE_STEP = 0.01    # Shift-scroll
ICON_UNMUTED = "audio-volume-high-symbolic"
ICON_MUTED = "audio-volume-muted-symbolic"
MIC_PIP_R = 2.3     # "microphone is open" dot, top-right of the speaker


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
        available = self.pulse.available
        muted = self.pulse.muted or not available
        # A mute reads as nothing coming out, the same way the mixer's slider
        # and percentage do. The level itself is untouched -- PulseAudio keeps
        # it behind the mute -- so unmuting brings the bar straight back.
        level = 0.0 if muted else self.pulse.volume
        if not available:
            colour = self.theme["fg_dim"]   # nothing to control: disabled
        elif muted:
            colour = self.theme["warn"]
        else:
            colour = self.theme["fg"]
        showing_bar = now() < self._flash_until
        cy = h / 2 - (2.5 if showing_bar else 0)
        scale = ICON / 17.0
        self._speaker(cr, w / 2, cy, scale, level, muted, colour)
        if self.pulse.mic_live:
            self._mic_pip(cr, w / 2, cy, scale)

        if showing_bar:
            # Fades out rather than vanishing, so the change reads as one gesture.
            t = (self._flash_until - now()) / FLASH_SEC
            alpha = ease_out_cubic(min(1.0, t * 3.0))
            bw = w - 12
            by = h - 7
            rgba(cr, with_alpha(self.theme["fg"], 0.15 * alpha))
            rounded_rect(cr, 6, by, bw, 3, 1.5)
            cr.fill()
            if muted:
                fill = colour
            else:
                fill = self.theme["crit"] if level > 1.0 else self.theme["accent"]
            rgba(cr, with_alpha(fill, alpha))
            # The 3px floor is what a zero level has always looked like, so a
            # mute lands exactly where dragging the slider to 0 does.
            rounded_rect(cr, 6, by, max(3, bw * min(1.0, level)), 3, 1.5)
            cr.fill()

    def _mic_pip(self, cr, cx, cy, s):
        """A dot saying the microphone is open.

        It is a separate mark rather than a tint on the speaker because the
        two states are independent: the output can be muted while the
        microphone is live, and a single colour cannot say both. It sits
        clear of the speaker's arcs, which reach cy±6.2s at their widest,
        and of the mute cross, which stops at cy-3.2s.
        """
        x, y = cx + 8.8 * s, cy - 8.6 * s
        # A ring of the bar's own colour keeps the dot legible where it
        # would otherwise touch the topmost arc.
        rgba(cr, self.theme["bg"], 1.0)
        cr.arc(x, y, (MIC_PIP_R + 1.1) * s, 0, math.tau)
        cr.fill()
        rgba(cr, self.theme["ok"])
        cr.arc(x, y, MIC_PIP_R * s, 0, math.tau)
        cr.fill()

    def tooltip(self):
        if not self.pulse.available:
            return "No sound server"
        if self.pulse.muted:
            text = "Muted"
        else:
            text = f"Volume {self.pulse.volume*100:.0f}%"
        if self.pulse.mic_live:
            text += "   ·   Microphone live"
        return text

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

    # -- adjustment --------------------------------------------------------
    # Both entry points -- a scroll on the icon and a media key routed through
    # the control socket -- come through here, so the level flash looks the
    # same however the volume was changed.
    def nudge(self, direction, fine=False):
        step = (FINE_STEP if fine else VOLUME_STEP) * direction
        self.pulse.step_volume(step)
        if self.pulse.muted and direction > 0:
            self.pulse.set_mute(False)
        self._flash()

    def toggle_mute(self):
        self.pulse.toggle_mute()
        self._flash()

    def on_scroll(self, direction, event):
        self.nudge(1 if direction > 0 else -1,
                   fine=bool(event.state & (1 << 0)))   # Shift = fine
        return True

    def on_click(self, button, x, y, event):
        if button == 2:
            self.toggle_mute()
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
        pop.content.get_style_context().add_class("td-popup")

        if not self.pulse.available:
            pop.content.pack_start(label("No sound server available", "td-dim"),
                                   False, False, 0)
            pop.open_at(self.dock.item_center_root(self))
            return pop

        def shown_volume():
            """What the mixer displays: muted reads as nothing coming out.

            PulseAudio keeps the level behind a mute, so this is display
            only -- unmuting brings the previous percentage straight back
            without us having to remember it.
            """
            return 0.0 if self.pulse.muted else self.pulse.volume

        head = Gtk.Box(spacing=8)
        head.pack_start(label("Output", "td-title"), False, False, 0)
        pct = label(f"{shown_volume()*100:.0f}%", "td-dim", xalign=1.0)
        head.pack_end(pct, False, False, 0)
        pop.content.pack_start(head, False, False, 0)

        row = Gtk.Box(spacing=8)
        mute_btn = Gtk.ToggleButton()
        mute_btn.set_active(self.pulse.muted)
        mute_btn.get_style_context().add_class("td-btn")
        # The icon is the state, not the action: a speaker while sound is
        # coming out, a crossed speaker while it is not.
        mute_img = Gtk.Image.new_from_icon_name(
            ICON_MUTED if self.pulse.muted else ICON_UNMUTED,
            Gtk.IconSize.BUTTON)
        mute_btn.add(mute_img)
        row.pack_start(mute_btn, False, False, 0)

        def show_mute_state():
            """Icon and colour together, so the button is a readout."""
            muted = self.pulse.muted
            mute_img.set_from_icon_name(ICON_MUTED if muted else ICON_UNMUTED,
                                        Gtk.IconSize.BUTTON)
            ctx = mute_btn.get_style_context()
            if muted:
                ctx.add_class("td-muted")
            else:
                ctx.remove_class("td-muted")

        show_mute_state()

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_value(shown_volume() * 100)
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

        # Widgets built further down that also have to follow the server.
        extra_sync = []

        def sync(*_a):
            # Reflect external changes (media keys, other apps) live.
            guard["busy"] = True
            level = shown_volume()
            scale.set_value(level * 100)
            mute_btn.set_active(self.pulse.muted)
            show_mute_state()
            pct.set_text(f"{level*100:.0f}%")
            for update in extra_sync:
                update()
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
                    lambda sw, _p: None if guard["busy"]
                    else self.pulse.set_mic_mute(not sw.get_active()))
        # So the mic-mute key moves the switch while the mixer is open.
        extra_sync.append(lambda: mic.set_active(not self.pulse.mic_mute))
        mic_row.pack_end(mic, False, False, 0)
        pop.content.pack_start(mic_row, False, False, 0)

        settings = Gtk.Button(label="Sound settings")
        settings.get_style_context().add_class("td-btn")
        settings.connect("clicked", lambda _b: (pop.dismiss(), self._open_mixer()))
        pop.content.pack_start(settings, False, False, 0)

        pop.open_at(self.dock.item_center_root(self))
        return pop

    def _open_mixer(self):
        if not launch_first(("pavucontrol", "pavucontrol-qt",
                             "gnome-control-center sound", "qpwgraph",
                             "helvum")):
            print("taldock: no volume mixer found (try installing pavucontrol)")
