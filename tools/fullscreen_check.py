#!/usr/bin/env python3
"""Does the dock get out of the way of a fullscreen window?

    tools/fullscreen_check.py

Opens a solid magenta window, fullscreens it on the primary monitor, and
samples the strip of screen where the bar lives: with `hide_for_fullscreen`
on it should be pure magenta. Then it leaves fullscreen and checks that the
bar is back. The window is our own, so nothing typed or clicked can land in
the user's session. Run it against the live dock -- constructing a second
`Dock` would take the tray away from it.
"""
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

MAGENTA = (255, 0, 255)
STRIP = 70          # px at the bottom of the monitor that the bar occupies
SETTLE_MS = 900     # WM round trip plus the 180ms slide


def strip(geo):
    root = Gdk.get_default_root_window()
    pb = Gdk.pixbuf_get_from_window(root, geo.x, geo.y + geo.height - STRIP,
                                    geo.width, STRIP)
    if pb is None:
        sys.exit("fullscreen_check: nothing captured (screen blanked?)")
    data, stride, n = pb.get_pixels(), pb.get_rowstride(), pb.get_n_channels()
    return [tuple(data[y * stride + x * n:y * stride + x * n + 3])
            for y in range(0, STRIP, 5) for x in range(0, geo.width, 7)]


def magenta_fraction(px):
    return sum(p == MAGENTA for p in px) / len(px)


def same_fraction(a, b):
    """Share of samples unchanged; the clock and gauges move, little else."""
    return sum(p == q for p, q in zip(a, b)) / len(a)


def main():
    mon = Gdk.Display.get_default().get_primary_monitor() \
        or Gdk.Display.get_default().get_monitor(0)
    geo = mon.get_geometry()
    win = Gtk.Window(title="taldock fullscreen check")
    css = Gtk.CssProvider()
    css.load_from_data(b"window { background: #ff00ff; }")
    win.get_style_context().add_provider(
        css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win.move(geo.x + 100, geo.y + 100)
    win.resize(400, 300)
    results = {}

    def step(name, action, then):
        def run():
            action()
            GLib.timeout_add(SETTLE_MS, measure)
            return False

        def measure():
            results[name] = strip(geo)
            then()
            return False
        return run

    def done():
        win.destroy()
        Gtk.main_quit()

    leave = step("windowed", win.unfullscreen, done)
    enter = step("fullscreen", win.fullscreen, lambda: GLib.idle_add(leave))
    win.show_all()

    def baseline():
        results["before"] = strip(geo)
        return enter()
    GLib.timeout_add(SETTLE_MS, baseline)
    Gtk.main()

    full = magenta_fraction(results["fullscreen"])
    back = same_fraction(results["before"], results["windowed"])
    print(f"bar strip magenta while fullscreen:   {full:6.1%}  (want 100%)")
    print(f"bar strip as before, after leaving:   {back:6.1%}  (want ~100%)")
    ok = full > 0.999 and back > 0.9
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
