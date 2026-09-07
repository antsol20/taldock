"""Shared helpers: cairo drawing primitives, icon loading, desktop entries."""
from __future__ import annotations

import math
import os
import re
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

# --------------------------------------------------------------------------
# cairo primitives
# --------------------------------------------------------------------------


def rounded_rect(cr, x, y, w, h, r):
    """Append a rounded-rectangle sub-path. Radius is clamped to fit."""
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    if r <= 0.01:
        cr.rectangle(x, y, w, h)
        return
    k = math.pi / 2.0
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -k, 0)
    cr.arc(x + w - r, y + h - r, r, 0, k)
    cr.arc(x + r, y + h - r, r, k, 2 * k)
    cr.arc(x + r, y + r, r, 2 * k, 3 * k)
    cr.close_path()


def rect(x, y, w, h):
    """Build a Gdk.Rectangle.

    Gdk.Rectangle is a boxed struct: PyGObject silently ignores constructor
    arguments, so the fields must be assigned after construction.
    """
    r = Gdk.Rectangle()
    r.x, r.y, r.width, r.height = int(x), int(y), int(w), int(h)
    return r


def rgba(cr, col, alpha_scale=1.0):
    """Set source from an (r, g, b, a) tuple with 0..1 components."""
    r, g, b, a = col
    cr.set_source_rgba(r, g, b, a * alpha_scale)


def mix(c1, c2, t):
    """Linear blend between two rgba tuples."""
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def hex_rgba(text, alpha=1.0):
    """Parse '#rrggbb' / '#rgb' / '#rrggbbaa' into an rgba tuple."""
    s = text.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 8:
        alpha = int(s[6:8], 16) / 255.0
        s = s[:6]
    return (
        int(s[0:2], 16) / 255.0,
        int(s[2:4], 16) / 255.0,
        int(s[4:6], 16) / 255.0,
        alpha,
    )


def with_alpha(col, a):
    return (col[0], col[1], col[2], a)


def draw_shadow(cr, x, y, w, h, radius, spread, col=(0, 0, 0, 0.30)):
    """Cheap layered drop shadow. Cost is O(spread), so keep spread small."""
    steps = max(1, int(spread))
    for i in range(steps, 0, -1):
        t = i / steps
        cr.set_source_rgba(col[0], col[1], col[2], col[3] * (1.0 - t) ** 2 / steps * 3.0)
        rounded_rect(cr, x - i, y - i * 0.5, w + i * 2, h + i, radius + i)
        cr.fill()


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t, overshoot=1.70158):
    t = max(0.0, min(1.0, t)) - 1.0
    return t * t * ((overshoot + 1) * t + overshoot) + 1.0


def now():
    return time.monotonic()


# --------------------------------------------------------------------------
# launching helpers
# --------------------------------------------------------------------------

TERMINALS = ["x-terminal-emulator", "xfce4-terminal", "alacritty", "kitty",
             "gnome-terminal", "konsole", "xterm"]


def launch_first(commands):
    """Spawn the first command whose binary actually exists.

    Do not be tempted by ["/usr/bin/env", cmd]: that always spawns
    successfully because /usr/bin/env exists, and the failure happens in the
    child where no exception can reach us -- so a fallback list built that
    way never gets past its first entry.
    """
    for command in commands:
        argv = [command] if isinstance(command, str) else list(command)
        binary = GLib.find_program_in_path(argv[0])
        if binary is None:
            continue
        argv[0] = binary
        try:
            GLib.spawn_async(argv, flags=GLib.SpawnFlags.SEARCH_PATH)
            return True
        except GLib.Error as exc:
            print(f"taldock: could not launch {argv[0]}: {exc}")
    return False


def launch_in_terminal(argv):
    """Run a console program in whichever terminal emulator is installed."""
    for terminal in TERMINALS:
        binary = GLib.find_program_in_path(terminal)
        if binary is None:
            continue
        try:
            GLib.spawn_async([binary, "-e"] + list(argv),
                             flags=GLib.SpawnFlags.SEARCH_PATH)
            return True
        except GLib.Error:
            continue
    return False


# --------------------------------------------------------------------------
# icons
# --------------------------------------------------------------------------


class IconCache:
    """Icon-name/GIcon -> cairo surface, memoised per (key, size, scale)."""

    def __init__(self):
        self._surfaces = {}
        self._theme = Gtk.IconTheme.get_default()
        self._theme.connect("changed", lambda *_: self.invalidate())

    def invalidate(self):
        self._surfaces.clear()

    # -- lookup ------------------------------------------------------------
    def _pixbuf_for(self, icon, size, scale):
        """Resolve an icon name, path, or GIcon to a pixbuf at `size` logical px."""
        px = size * scale
        flags = Gtk.IconLookupFlags.FORCE_SIZE | Gtk.IconLookupFlags.USE_BUILTIN
        try:
            if isinstance(icon, str):
                if icon.startswith("/") or icon.startswith("~"):
                    path = os.path.expanduser(icon)
                    if os.path.exists(path):
                        return GdkPixbuf.Pixbuf.new_from_file_at_size(path, px, px)
                    return None
                # Strip a trailing extension: some .desktop files ship "foo.png".
                name = re.sub(r"\.(png|svg|xpm)$", "", icon)
                info = self._theme.lookup_icon_for_scale(name, size, scale, flags)
                if info is None:
                    info = self._theme.lookup_icon_for_scale(
                        name.lower(), size, scale, flags
                    )
                if info is None:
                    return None
                return info.load_icon()
            if isinstance(icon, Gio.Icon):
                info = self._theme.lookup_by_gicon_for_scale(icon, size, scale, flags)
                return info.load_icon() if info else None
        except GLib.Error:
            return None
        return None

    def surface(self, icon, size, scale=1, fallback="application-x-executable"):
        """Return a cairo surface for `icon`, or a themed fallback."""
        key = (icon if isinstance(icon, str) else icon.to_string(), size, scale)
        if key in self._surfaces:
            return self._surfaces[key]
        pb = self._pixbuf_for(icon, size, scale) if icon else None
        if pb is None and fallback:
            pb = self._pixbuf_for(fallback, size, scale)
        surf = None
        if pb is not None:
            if pb.get_width() != size * scale or pb.get_height() != size * scale:
                pb = pb.scale_simple(
                    size * scale, size * scale, GdkPixbuf.InterpType.BILINEAR
                )
            surf = Gdk.cairo_surface_create_from_pixbuf(pb, scale, None)
        self._surfaces[key] = surf
        return surf

    def surface_from_pixbuf(self, pb, size, scale=1, key=None):
        """Same, for a pixbuf we already hold (e.g. a window's _NET_WM_ICON)."""
        if pb is None:
            return None
        ck = (key, size, scale)
        if key is not None and ck in self._surfaces:
            return self._surfaces[ck]
        px = size * scale
        if pb.get_width() != px or pb.get_height() != px:
            pb = pb.scale_simple(px, px, GdkPixbuf.InterpType.BILINEAR)
        surf = Gdk.cairo_surface_create_from_pixbuf(pb, scale, None)
        if key is not None:
            self._surfaces[ck] = surf
        return surf


ICONS = IconCache()


def paint_surface(cr, surf, x, y, size, scale=1, alpha=1.0):
    """Draw a cached surface at logical position/size with optional alpha."""
    if surf is None:
        return
    cr.save()
    cr.translate(x, y)
    sw = surf.get_width() / scale
    if sw > 0 and abs(sw - size) > 0.5:
        cr.scale(size / sw, size / sw)
    cr.set_source_surface(surf, 0, 0)
    if alpha >= 0.999:
        cr.paint()
    else:
        cr.paint_with_alpha(alpha)
    cr.restore()
