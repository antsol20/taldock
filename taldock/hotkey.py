"""Passive X11 key grab that reports release as well as press.

XFCE's shortcut mechanism -- xfconf `/commands/custom/<accel>`, which is how
the Super key and the media keys are bound -- runs a command on key *press*
and says nothing at all about release. Push-to-talk needs both edges, so
talflow grabs its combination from the X server directly.

Two things make this safe here, where CLAUDE.md warns that `XGrabKey` on
`Super_L` would break every Super+key combo:

* We grab a *non-modifier* key with a modifier mask, so the resulting active
  grab lasts only from the press of that one key to its release. Nothing
  else the user types is ever redirected.
* `XkbSetDetectableAutoRepeat` is set on our own connection, so a held key
  delivers repeated KeyPress events with **no** interleaved KeyRelease and
  exactly one real KeyRelease at the end. Without it a 700ms hold looks like
  48 press/release pairs and there is no way to tell a repeat from letting
  go. Measured on this machine: 48 presses, 1 release.

The grab lives on its own display connection, pumped by a GLib watch on its
file descriptor, because PyGObject exposes neither `Gdk.Window.add_filter`
nor any other route to raw X events -- the same reason `x11.py` exists.
"""
from __future__ import annotations

import ctypes as C

import gi

# Pinned here as well as in dock.py: this module is imported by tools that
# have not loaded GTK yet, and under GTK 4 `accelerator_parse` returns three
# values instead of two, so an unpinned import fails only at parse time.
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

KEY_PRESS = 2
KEY_RELEASE = 3
GRAB_MODE_ASYNC = 1
BAD_ACCESS = 10

SHIFT_MASK = 1 << 0
LOCK_MASK = 1 << 1
CONTROL_MASK = 1 << 2
MOD1_MASK = 1 << 3

# Gdk.ModifierType bits that are *virtual*: they have no fixed X mask and
# must be resolved through the server's modifier map.
GDK_SUPER = 1 << 26
GDK_HYPER = 1 << 27
GDK_META = 1 << 28


class XKeyEvent(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), ("window", C.c_ulong), ("root", C.c_ulong),
                ("subwindow", C.c_ulong), ("time", C.c_ulong),
                ("x", C.c_int), ("y", C.c_int),
                ("x_root", C.c_int), ("y_root", C.c_int),
                ("state", C.c_uint), ("keycode", C.c_uint), ("same_screen", C.c_int)]


class XEvent(C.Union):
    # An XEvent is 24 longs; only the key fields are ever read.
    _fields_ = [("type", C.c_int), ("key", XKeyEvent), ("pad", C.c_long * 24)]


class XErrorEvent(C.Structure):
    _fields_ = [("type", C.c_int), ("display", C.c_void_p),
                ("resourceid", C.c_ulong), ("serial", C.c_ulong),
                ("error_code", C.c_ubyte), ("request_code", C.c_ubyte),
                ("minor_code", C.c_ubyte)]


class XModifierKeymap(C.Structure):
    _fields_ = [("max_keypermod", C.c_int), ("modifiermap", C.POINTER(C.c_ubyte))]


ERROR_HANDLER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(XErrorEvent))

# Module-level so the trampolines are never garbage collected: Xlib keeps
# raw pointers to them and a collected closure segfaults the dock.
_error_handler = None
_previous_handler = None
_swallowed = []


def _install_error_handler(lib):
    """Chain an error handler that swallows our own grab failures.

    Xlib's default handler calls exit() on any protocol error, which would
    take the whole dock down if a hotkey is already held by another client.
    The handler is global to the process, so GDK's is saved and everything
    that is not ours is passed straight through to it.
    """
    global _error_handler, _previous_handler
    if _error_handler is not None:
        return

    def handle(display, event):
        err = event.contents
        _swallowed.append((err.error_code, err.request_code))
        if _previous_handler and err.error_code != BAD_ACCESS:
            return _previous_handler(display, event)
        return 0

    _error_handler = ERROR_HANDLER(handle)
    _previous_handler = lib.XSetErrorHandler(_error_handler)


class HotkeyGrabber(GObject.Object):
    """Grabs one accelerator and emits `pressed` / `released` for it."""

    __gsignals__ = {
        "pressed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "released": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, accel):
        super().__init__()
        self.accel = accel
        self.error = None
        self._lib = None
        self._display = None
        self._watch = 0
        self._keycode = 0
        self._masks = ()
        self._down = False
        self._open()
        if self._lib is not None:
            self.bind(accel)

    # -- setup -------------------------------------------------------------
    def _open(self):
        try:
            lib = C.CDLL("libX11.so.6")
        except OSError as exc:
            self.error = f"libX11 unavailable: {exc}"
            return
        lib.XOpenDisplay.restype = C.c_void_p
        lib.XOpenDisplay.argtypes = [C.c_char_p]
        lib.XSetErrorHandler.restype = ERROR_HANDLER
        lib.XSetErrorHandler.argtypes = [ERROR_HANDLER]
        lib.XStringToKeysym.restype = C.c_ulong
        lib.XStringToKeysym.argtypes = [C.c_char_p]
        lib.XKeysymToKeycode.restype = C.c_ubyte
        lib.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
        lib.XGetModifierMapping.restype = C.POINTER(XModifierKeymap)
        lib.XGetModifierMapping.argtypes = [C.c_void_p]
        lib.XFreeModifiermap.argtypes = [C.POINTER(XModifierKeymap)]
        lib.XDefaultRootWindow.restype = C.c_ulong
        lib.XDefaultRootWindow.argtypes = [C.c_void_p]
        lib.XGrabKey.argtypes = [C.c_void_p, C.c_int, C.c_uint, C.c_ulong,
                                 C.c_int, C.c_int, C.c_int]
        lib.XUngrabKey.argtypes = [C.c_void_p, C.c_int, C.c_uint, C.c_ulong]
        lib.XConnectionNumber.restype = C.c_int
        lib.XConnectionNumber.argtypes = [C.c_void_p]
        lib.XPending.restype = C.c_int
        lib.XPending.argtypes = [C.c_void_p]
        lib.XNextEvent.argtypes = [C.c_void_p, C.POINTER(XEvent)]
        lib.XSync.argtypes = [C.c_void_p, C.c_int]
        lib.XkbSetDetectableAutoRepeat.restype = C.c_int
        lib.XkbSetDetectableAutoRepeat.argtypes = [C.c_void_p, C.c_int,
                                                   C.POINTER(C.c_int)]
        display = lib.XOpenDisplay(None)
        if not display:
            self.error = "no X display"
            return
        _install_error_handler(lib)
        self._lib = lib
        self._display = display
        self._root = lib.XDefaultRootWindow(display)

        supported = C.c_int(0)
        lib.XkbSetDetectableAutoRepeat(display, True, C.byref(supported))
        if not supported.value:
            # Every autorepeat would arrive as a press/release pair, making a
            # long hold indistinguishable from a rapid series of taps.
            self.error = "detectable autorepeat unsupported"

        self._watch = GLib.io_add_watch(
            GLib.IOChannel.unix_new(lib.XConnectionNumber(display)),
            GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, self._on_readable)

    def _modifier_mask_for(self, keysym_name):
        """Which of Mod1..Mod5 carries a given modifier keysym, if any."""
        lib = self._lib
        keycode = lib.XKeysymToKeycode(
            self._display, lib.XStringToKeysym(keysym_name.encode()))
        if not keycode:
            return 0
        mapping = lib.XGetModifierMapping(self._display)
        if not mapping:
            return 0
        per = mapping.contents.max_keypermod
        mask = 0
        for index in range(8):
            for slot in range(per):
                if mapping.contents.modifiermap[index * per + slot] == keycode:
                    mask = 1 << index
        lib.XFreeModifiermap(mapping)
        return mask

    # -- binding -----------------------------------------------------------
    def parse(self, accel):
        """"<Primary><Super>space" -> (keycode, x modifier mask), or None.

        Gtk parses the string; the virtual Super/Hyper/Meta bits it returns
        are then resolved to real modifiers through the server's map, since
        X knows only Mod1..Mod5.
        """
        keyval, mods = Gtk.accelerator_parse(accel)
        if not keyval:
            return None
        keycode = self._lib.XKeysymToKeycode(self._display, keyval)
        if not keycode:
            return None
        mask = int(mods) & (SHIFT_MASK | CONTROL_MASK | MOD1_MASK
                            | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7))
        if mods & GDK_SUPER:
            mask |= self._modifier_mask_for("Super_L")
        if mods & GDK_HYPER:
            mask |= self._modifier_mask_for("Hyper_L")
        if mods & GDK_META:
            mask |= self._modifier_mask_for("Meta_L") or MOD1_MASK
        return keycode, mask

    def bind(self, accel):
        """Grab `accel`. Returns True, or False with `self.error` set."""
        if self._lib is None:
            return False
        self.unbind()
        parsed = self.parse(accel)
        if parsed is None:
            self.error = f"cannot parse shortcut {accel!r}"
            return False
        keycode, mask = parsed
        # The lock modifiers are part of the grab's modifier match, so a
        # combination has to be grabbed once for every state Caps and Num
        # Lock can be in or it silently stops working with NumLock on.
        num_lock = self._modifier_mask_for("Num_Lock")
        scroll_lock = self._modifier_mask_for("Scroll_Lock")
        extras = {0, LOCK_MASK, num_lock, scroll_lock,
                  LOCK_MASK | num_lock, LOCK_MASK | scroll_lock,
                  num_lock | scroll_lock, LOCK_MASK | num_lock | scroll_lock}
        del _swallowed[:]
        masks = []
        for extra in sorted(extras):
            self._lib.XGrabKey(self._display, keycode, mask | extra,
                               self._root, False, GRAB_MODE_ASYNC,
                               GRAB_MODE_ASYNC)
            masks.append(mask | extra)
        self._lib.XSync(self._display, False)
        if any(code == BAD_ACCESS for code, _req in _swallowed):
            # Another client already owns the combination; ours will never
            # fire, so report it rather than looking broken.
            self.unbind_masks(keycode, masks)
            self.error = f"{accel} is already taken by another application"
            return False
        self.accel = accel
        self._keycode = keycode
        self._masks = tuple(masks)
        self.error = None
        return True

    def unbind_masks(self, keycode, masks):
        for mask in masks:
            self._lib.XUngrabKey(self._display, keycode, mask, self._root)
        self._lib.XSync(self._display, False)

    def unbind(self):
        if self._lib is None or not self._keycode:
            return
        self.unbind_masks(self._keycode, self._masks)
        self._keycode = 0
        self._masks = ()
        self._down = False

    @property
    def ok(self):
        return self._lib is not None and self._keycode != 0

    # -- events ------------------------------------------------------------
    def _on_readable(self, *_a):
        event = XEvent()
        while self._lib.XPending(self._display):
            self._lib.XNextEvent(self._display, C.byref(event))
            if event.key.keycode != self._keycode:
                continue
            if event.type == KEY_PRESS:
                # Autorepeat delivers further presses with no release; only
                # the first one is an edge.
                if not self._down:
                    self._down = True
                    self.emit("pressed")
            elif event.type == KEY_RELEASE:
                if self._down:
                    self._down = False
                    self.emit("released")
        return True

    def shutdown(self):
        if self._watch:
            GLib.source_remove(self._watch)
            self._watch = 0
        self.unbind()
        self._lib = self._display = None
