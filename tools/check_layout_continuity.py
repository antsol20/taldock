#!/usr/bin/env python3
"""Regression check: launcher magnification must be continuous.

The row's position has to be a continuous function of the pointer, or the
icons visibly jump as you sweep across them. An earlier version anchored the
row to the icon nearest the pointer and moved ~10px in a single motion event
each time a different icon became nearest.

Run with the dock's dependencies available:  python3 tools/check_layout_continuity.py
Exits non-zero if the layout regresses.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from taldock.dock import Dock  # noqa: E402

STEP = 0.5          # pointer movement between samples, px
TOLERANCE = 2.0     # largest acceptable icon movement per step, px

status = {"code": 1}


def run():
    while Gtk.events_pending():
        Gtk.main_iteration()
    zone = Dock_instance.launchers
    if len(zone.icons) < 2:
        print("SKIP: need at least two dock icons to test")
        status["code"] = 0
        Gtk.main_quit()
        return False

    Dock_instance._ensure_layout()
    zone.pointer_x = None
    zone._zoom_amount = 0.0
    zone.layout()
    left, right = zone.extent
    rest = [icon.x for icon in zone.icons]

    zone._zoom_amount = 1.0
    worst, worst_at, previous = 0.0, None, None
    x = left - 80
    while x < right + 80:
        zone.pointer_x = x
        zone.layout()
        positions = [icon.x for icon in zone.icons]
        if previous is not None:
            jump = max(abs(a - b) for a, b in zip(positions, previous))
            if jump > worst:
                worst, worst_at = jump, x
        previous = positions
        x += STEP

    # A pointer out in the empty part of the zone must leave the row alone.
    zone.pointer_x = zone.x + zone.width - 20
    zone.layout()
    parked = [icon.x for icon in zone.icons]

    print(f"icons={len(zone.icons)}  row={left:.0f}..{right:.0f}")
    print(f"largest jump per {STEP}px of pointer: {worst:.3f}px "
          f"(at x={worst_at})  tolerance {TOLERANCE}px")
    print(f"row parked when pointer is far away: {parked == rest}")

    ok = worst <= TOLERANCE and parked == rest
    print("PASS" if ok else "FAIL")
    status["code"] = 0 if ok else 1
    Gtk.main_quit()
    return False


Dock_instance = Dock()
Dock_instance.window.show_all()
GLib.timeout_add(600, run)
Gtk.main()
sys.exit(status["code"])
