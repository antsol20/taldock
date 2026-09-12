#!/usr/bin/env python3
"""Render every talflow icon state to one strip, to check they read apart.

    tools/talflow_states.py out.png

The icon is the entire user interface for dictation -- there is no waveform
overlay -- so the six states have to be told apart at panel size. Two of
them share the `crit` colour (recording, error); this is how you check the
pulse and the badge really do separate them. Rendered offline rather than
captured, because a running dock cannot be put into the failure states on
demand, and constructing a second Dock would take the tray away from the
real one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo                                          # noqa: E402
import gi                                             # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from taldock import talflow as tf                     # noqa: E402
from taldock.config import Config                     # noqa: E402
from taldock.theme import Theme                       # noqa: E402
from taldock.util import rgba, rounded_rect           # noqa: E402
from taldock.widgets.talflow import TalflowItem       # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "talflow-states.png"
CELL_W, CELL_H, PAD = 26, 54, 12

STATES = [tf.IDLE, tf.RECORDING, tf.SENDING, tf.PASTED, tf.CLIPBOARD, tf.ERROR]


class StubFlow:
    """Just enough of Talflow for the widget to paint."""
    def __init__(self):
        self.state = tf.IDLE
        self.ready = True


class StubDock:
    def __init__(self):
        self.cfg = Config(os.devnull)
        self.theme = Theme(self.cfg)
        self.scale = 1


class Rendered(TalflowItem):
    """Bypasses setup(): a real one would grab the running dock's hotkey."""
    def setup(self):
        self.flow = StubFlow()
        self._anim = 0


dock = StubDock()
item = Rendered(dock)

width = PAD + len(STATES) * (CELL_W + PAD)
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, CELL_H)
cr = cairo.Context(surface)
rgba(cr, dock.theme["bg"])
cr.paint()
rgba(cr, dock.theme["bg_hi"])
rounded_rect(cr, 0, 0, width, CELL_H, 0)
cr.fill()

for index, state in enumerate(STATES):
    item.flow.state = state
    cr.save()
    cr.translate(PAD + index * (CELL_W + PAD), 0)
    cr.rectangle(0, 0, CELL_W, CELL_H)
    cr.clip()
    item.draw(cr, CELL_W, CELL_H)
    cr.restore()

surface.write_to_png(OUT)
print(f"{OUT}  {width}x{CELL_H}  states: {', '.join(STATES)}")
