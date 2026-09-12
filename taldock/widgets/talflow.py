"""Dictation indicator: the whole user interface for talflow is this icon.

There is no waveform overlay by design -- the glyph's colour and its badge
are the entire readout, so the states have to be told apart at 17px and
without hovering. Colour carries the state and a badge disambiguates the two
that share one: recording and error are both `crit`, but recording is the
only state that pulses, and error is the only one wearing a cross.
"""
from __future__ import annotations

import math

from gi.repository import Gdk, GLib, Gtk

from .. import talflow as tf
from ..popup import Popup, label, separator
from ..util import now, rgba, rounded_rect, with_alpha
from .base import PanelItem

ICON = 17.0
BADGE_R = 3.0
PULSE_HZ = 1.15         # recording heartbeat
SPIN_HZ = 0.85          # sending arc
ANIM_MS = 40

#: state -> (palette key, badge). A badge of None draws the bare microphone.
LOOK = {
    tf.IDLE:      ("fg", None),
    tf.RECORDING: ("crit", None),
    tf.SENDING:   ("accent", None),
    tf.PASTED:    ("ok", "check"),
    tf.CLIPBOARD: ("warn", "clipboard"),
    tf.ERROR:     ("crit", "cross"),
}


class TalflowItem(PanelItem):
    def setup(self):
        self.flow = tf.Talflow()
        self.flow.connect("state-changed", self._on_state)
        self._anim = 0

    def destroy(self):
        self._stop_anim()
        self.flow.shutdown()
        super().destroy()

    def measure(self, height):
        return 26

    # -- animation ---------------------------------------------------------
    def _on_state(self, *_a):
        if self.flow.state in (tf.RECORDING, tf.SENDING):
            self._start_anim()
        else:
            self._stop_anim()
        self.redraw()

    def _start_anim(self):
        if not self._anim:
            self._anim = GLib.timeout_add(ANIM_MS, self._tick_anim)

    def _stop_anim(self):
        if self._anim:
            GLib.source_remove(self._anim)
            self._anim = 0

    def _tick_anim(self):
        self.redraw()
        if self.flow.state not in (tf.RECORDING, tf.SENDING):
            self._anim = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- glyph -------------------------------------------------------------
    def _microphone(self, cr, cx, cy, s, colour):
        """Capsule head, cradle arc, stem. Filled, so it reads when small."""
        rgba(cr, colour)
        rounded_rect(cr, cx - 2.9 * s, cy - 7.0 * s, 5.8 * s, 7.6 * s, 2.9 * s)
        cr.fill()
        cr.set_line_width(1.4 * s)
        cr.set_line_cap(1)   # ROUND
        cr.arc(cx, cy - 0.9 * s, 4.9 * s, 0.18 * math.pi, 0.82 * math.pi)
        cr.stroke()
        cr.move_to(cx, cy + 4.2 * s)
        cr.line_to(cx, cy + 6.3 * s)
        cr.stroke()

    def _badge(self, cr, cx, cy, s, kind, colour):
        """A small mark top-right, ringed in bar colour so it stays legible."""
        # Far enough out that the ring of bar colour, not the badge itself,
        # is what overlaps the capsule -- the same trick the audio widget's
        # live-microphone dot uses to stay legible against the speaker arcs.
        x, y = cx + 6.4 * s, cy - 7.0 * s
        rgba(cr, self.theme["bg"], 1.0)
        cr.arc(x, y, (BADGE_R + 1.15) * s, 0, math.tau)
        cr.fill()
        rgba(cr, colour)
        if kind == "check":
            cr.arc(x, y, BADGE_R * s, 0, math.tau)
            cr.fill()
            rgba(cr, self.theme["bg"], 1.0)
            cr.set_line_width(1.1 * s)
            cr.set_line_cap(1)
            cr.move_to(x - 1.4 * s, y + 0.1 * s)
            cr.line_to(x - 0.35 * s, y + 1.2 * s)
            cr.line_to(x + 1.5 * s, y - 1.3 * s)
            cr.stroke()
        elif kind == "cross":
            cr.arc(x, y, BADGE_R * s, 0, math.tau)
            cr.fill()
            rgba(cr, self.theme["bg"], 1.0)
            cr.set_line_width(1.1 * s)
            cr.set_line_cap(1)
            cr.move_to(x - 1.25 * s, y - 1.25 * s)
            cr.line_to(x + 1.25 * s, y + 1.25 * s)
            cr.move_to(x + 1.25 * s, y - 1.25 * s)
            cr.line_to(x - 1.25 * s, y + 1.25 * s)
            cr.stroke()
        elif kind == "clipboard":
            # Same filled disc as the other two badges, so the three read as
            # one family, with a card cut out of it. Not a literal clipboard:
            # at four pixels across the clip and the board are one smudge,
            # and amber is already carrying the meaning.
            cr.arc(x, y, BADGE_R * s, 0, math.tau)
            cr.fill()
            rgba(cr, self.theme["bg"], 1.0)
            rounded_rect(cr, x - 0.95 * s, y - 1.35 * s, 1.9 * s, 2.7 * s, 0.5 * s)
            cr.fill()

    def draw(self, cr, w, h):
        self.draw_plate(cr, w, h)
        state = self.flow.state
        key, badge = LOOK.get(state, LOOK[tf.IDLE])
        colour = self.theme[key]
        if state == tf.IDLE and not self.flow.ready:
            colour = self.theme["fg_dim"]   # not armed: nothing will happen
        cx, cy = w / 2, h / 2
        s = ICON / 17.0

        if state == tf.RECORDING:
            # A slow heartbeat behind the glyph: the one state where the
            # machine is listening, so it should be visible from the corner
            # of the eye without reading the colour.
            beat = 0.5 + 0.5 * math.sin(now() * math.tau * PULSE_HZ)
            rgba(cr, with_alpha(colour, 0.13 + 0.17 * beat))
            cr.arc(cx, cy, (8.6 + 1.6 * beat) * s, 0, math.tau)
            cr.fill()
        elif state == tf.SENDING:
            # A single arc chasing its tail: work in flight, duration unknown.
            start = (now() * SPIN_HZ) * math.tau
            rgba(cr, with_alpha(colour, 0.85))
            cr.set_line_width(1.5 * s)
            cr.set_line_cap(1)
            cr.arc(cx, cy, 9.2 * s, start, start + math.pi * 0.62)
            cr.stroke()

        self._microphone(cr, cx, cy, s, colour)
        if badge:
            self._badge(cr, cx, cy, s, badge, colour)

    # -- text --------------------------------------------------------------
    def tooltip(self):
        flow = self.flow
        shortcut = flow.describe_shortcut()
        if flow.state == tf.RECORDING:
            return f"Recording…   ·   release {shortcut} to transcribe"
        if flow.state == tf.SENDING:
            return "Transcribing…"
        if flow.state == tf.ERROR:
            return f"Dictation failed: {flow.message}"
        if flow.state == tf.CLIPBOARD:
            return f"Transcript on the clipboard ({flow.message})"
        if flow.state == tf.PASTED:
            return flow.message or "Typed"
        if not flow.ready:
            return f"Dictation unavailable: {flow.message or 'not armed'}"
        if flow.message:
            # Usually "nothing heard", which is what a muted microphone
            # looks like from here.
            return f"{flow.message.capitalize()}   ·   hold {shortcut}"
        if flow.warning:
            return f"Dictation   ·   hold {shortcut}   ·   {flow.warning}"
        return f"Dictation   ·   hold {shortcut}"

    # -- input -------------------------------------------------------------
    def on_click(self, button, x, y, event):
        if button != 1:
            return False
        if self.popup:
            self.close_popup()
            return True
        self.open_popup(self._build_popup())
        return True

    def _input_name(self):
        """The capture device, flagged when it is muted.

        Dictating into a muted microphone yields "nothing heard" and still
        spends a request, and nothing else on the bar says the input is off.
        """
        pulse = self.dock.pulse
        name = pulse.mic_description or "default input"
        return f"{name}  (muted)" if pulse.mic_mute else name

    def _build_popup(self):
        flow = self.flow
        pop = Popup(self.dock, padding=13)
        pop.content.set_size_request(292, -1)
        pop.content.get_style_context().add_class("td-popup")

        head = Gtk.Box(spacing=8)
        head.pack_start(label("Dictation", "td-title"), False, False, 0)
        state_label = label("", "td-dim", xalign=1.0)
        head.pack_end(state_label, False, False, 0)
        pop.content.pack_start(head, False, False, 0)

        body = label("", ellipsize=False)
        body.set_line_wrap(True)
        body.set_selectable(True)
        # A selectable label takes focus when the popup is shown and selects
        # its whole contents, so the placeholder opened highlighted as though
        # something were wrong. Selecting by hand still works.
        body.set_can_focus(False)
        body.set_max_width_chars(38)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(38)
        scroller.set_max_content_height(132)
        scroller.set_propagate_natural_height(True)
        scroller.add(body)
        pop.content.pack_start(scroller, False, False, 2)

        actions = Gtk.Box(spacing=6)
        copy = Gtk.Button(label="Copy")
        copy.get_style_context().add_class("td-btn")
        copy.connect("clicked", lambda _b: (
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(
                flow.last_text, -1), pop.dismiss()))
        actions.pack_start(copy, False, False, 0)
        reload_btn = Gtk.Button(label="Reload settings")
        reload_btn.get_style_context().add_class("td-btn")
        # The API key is pasted into the file by hand, so re-reading it must
        # not mean restarting the dock.
        reload_btn.connect("clicked", lambda _b: (flow.reload(), pop.dismiss()))
        actions.pack_end(reload_btn, False, False, 0)
        pop.content.pack_start(actions, False, False, 2)

        pop.content.pack_start(separator(), False, False, 4)
        for name, value in (
                ("Model", flow.settings["model"]),
                ("Input", self._input_name()),
                ("Hold", flow.describe_shortcut())):
            row = Gtk.Box(spacing=8)
            row.pack_start(label(name, "td-dim"), False, False, 0)
            row.pack_end(label(value, xalign=1.0), True, True, 0)
            pop.content.pack_start(row, False, False, 0)

        if flow.warning:
            note = label(flow.warning, "td-dim", ellipsize=False)
            note.set_line_wrap(True)
            note.set_max_width_chars(38)
            note.set_margin_top(4)
            pop.content.pack_start(note, False, False, 0)

        def sync(*_a):
            state_label.set_text({
                tf.RECORDING: "recording…",
                tf.SENDING: "transcribing…",
                tf.PASTED: "typed",
                tf.CLIPBOARD: "on clipboard",
                tf.ERROR: "failed",
            }.get(flow.state, "ready" if flow.ready else "not armed"))
            if flow.state == tf.ERROR:
                body.set_text(flow.message)
            elif flow.last_text:
                body.set_text(flow.last_text)
            else:
                body.set_text(f"Hold {flow.describe_shortcut()} and speak.")
            body.select_region(0, 0)
            copy.set_sensitive(bool(flow.last_text))

        sync()
        handler = flow.connect("state-changed", sync)
        pop.connect("destroy", lambda *_a: flow.disconnect(handler))
        # Opening the popup is the acknowledgement for a sticky failure.
        pop.connect("dismissed", lambda *_a: flow.clear())

        pop.open_at(self.dock.item_center_root(self))
        return pop
