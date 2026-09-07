"""Small ctypes shim over libX11 for the bits GDK does not expose.

PyGObject's GTK3 bindings omit Gdk.property_change and Gdk event filters, so
strut reservation is done through Xlib directly. A private display
connection is used: X properties are server-side state, so setting them from
a second connection is equivalent to setting them from GDK's own.
"""
from __future__ import annotations

import ctypes as C

PropModeReplace = 0
XA_CARDINAL = 6
XA_ATOM = 4


class _Xlib:
    def __init__(self):
        self.lib = None
        self.display = None
        try:
            lib = C.CDLL("libX11.so.6")
        except OSError:
            return
        lib.XOpenDisplay.restype = C.c_void_p
        lib.XOpenDisplay.argtypes = [C.c_char_p]
        lib.XInternAtom.restype = C.c_ulong
        lib.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
        lib.XChangeProperty.argtypes = [
            C.c_void_p, C.c_ulong, C.c_ulong, C.c_ulong, C.c_int, C.c_int,
            C.c_void_p, C.c_int]
        lib.XDeleteProperty.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong]
        lib.XFlush.argtypes = [C.c_void_p]
        lib.XCloseDisplay.argtypes = [C.c_void_p]
        display = lib.XOpenDisplay(None)
        if not display:
            return
        self.lib = lib
        self.display = display
        self._atoms = {}

    @property
    def ok(self):
        return self.lib is not None and self.display is not None

    def atom(self, name):
        if name not in self._atoms:
            self._atoms[name] = self.lib.XInternAtom(
                self.display, name.encode(), False)
        return self._atoms[name]

    def set_cardinals(self, xid, name, values):
        """Set a 32-bit CARDINAL array property.

        Xlib's format-32 API takes an array of C long, which is 64-bit here;
        it narrows to 32 bits on the wire itself.
        """
        if not self.ok:
            return False
        array = (C.c_long * len(values))(*values)
        self.lib.XChangeProperty(
            self.display, xid, self.atom(name), XA_CARDINAL, 32,
            PropModeReplace, C.byref(array), len(values))
        self.lib.XFlush(self.display)
        return True

    def delete(self, xid, name):
        if not self.ok:
            return False
        self.lib.XDeleteProperty(self.display, xid, self.atom(name))
        self.lib.XFlush(self.display)
        return True

    def close(self):
        if self.ok:
            self.lib.XCloseDisplay(self.display)
            self.display = None


X = _Xlib()


def set_strut(xid, position, thickness, start, end, screen_height, screen_width):
    """Reserve `thickness` px along one screen edge for the window.

    `start`/`end` bound the reserved span along that edge, which keeps
    maximised windows correct on multi-monitor setups.
    """
    if not X.ok:
        return False
    left = right = top = bottom = 0
    # _NET_WM_STRUT_PARTIAL: 4 thicknesses then 8 start/end pairs.
    partial = [0] * 12
    if position == "top":
        top = thickness
        partial[2] = thickness
        partial[8], partial[9] = start, end
    else:
        bottom = thickness
        partial[3] = thickness
        partial[10], partial[11] = start, end
    X.set_cardinals(xid, "_NET_WM_STRUT_PARTIAL", partial)
    X.set_cardinals(xid, "_NET_WM_STRUT", [left, right, top, bottom])
    return True


def clear_strut(xid):
    X.delete(xid, "_NET_WM_STRUT_PARTIAL")
    X.delete(xid, "_NET_WM_STRUT")
