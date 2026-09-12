"""Synthetic typing through XTest, plus the two X queries talflow needs.

Why typing rather than the clipboard: a transcript has to land in whatever
had focus, and that is usually a terminal. Alacritty 0.16 binds paste to
Ctrl+Shift+V and leaves Ctrl+V unbound, so a synthesised Ctrl+V is passed
through to the pty as the raw byte 0x16 -- readline's `quoted-insert` --
which pastes nothing and leaves the shell waiting to insert the next
keystroke literally. xfce4-terminal behaves the same way. Characters typed
as keystrokes need no such agreement, and leave the clipboard alone.

Anything the current layout cannot produce (an em dash, a curly quote) is
typed by binding it to an unused keycode, which is how xdotool does it.
Each distinct character gets its **own** keycode, bound once before any of
them is typed and released only after the whole transcript has drained.
Reusing one scratch keycode does not work, and fails in a way that looks
like event ordering should have prevented: Xlib refreshes its keymap cache
as soon as it reads the MappingNotify off the socket, which can happen while
draining the connection and therefore before the application dispatches the
key events that were generated earlier. Rebinding mid-transcript made an em
dash arrive as the smart quote that came after it -- verified, then fixed by
never rebinding while keystrokes are in flight.

Typing runs off a GLib timeout rather than a loop, because the dock draws
its own bar: a 200-character transcript at one keystroke per 4ms would
otherwise freeze the panel for the best part of a second.
"""
from __future__ import annotations

import ctypes as C

from gi.repository import GLib

NO_SYMBOL = 0
XA_WINDOW = 33
SUCCESS = 0

# Level-3/4 (AltGr) entries are reached through the scratch keycode instead
# of by synthesising the level shift, which varies between layouts.
LEVEL_PLAIN = 0
LEVEL_SHIFT = 1

SPECIAL = {
    "\n": 0xFF0D,   # Return
    "\r": 0xFF0D,
    "\t": 0xFF09,   # Tab
}

MOD_KEYSYMS = ("Shift_L", "Shift_R", "Control_L", "Control_R",
               "Alt_L", "Alt_R", "Super_L", "Super_R", "ISO_Level3_Shift")


def keysym_for(char):
    """Unicode code point to X keysym, per the Xlib convention."""
    if char in SPECIAL:
        return SPECIAL[char]
    code = ord(char)
    if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF:
        return code            # Latin-1 keysyms are the code point itself
    return 0x01000000 + code


class Typist:
    """Owns an X connection and turns text into XTest key events."""

    def __init__(self):
        self.error = None
        self.lib = None
        self.xtst = None
        self.display = None
        self._job = None
        self._open()

    def _open(self):
        try:
            lib = C.CDLL("libX11.so.6")
            xtst = C.CDLL("libXtst.so.6")
        except OSError as exc:
            self.error = f"XTest unavailable: {exc}"
            return
        lib.XOpenDisplay.restype = C.c_void_p
        lib.XOpenDisplay.argtypes = [C.c_char_p]
        lib.XDisplayKeycodes.argtypes = [C.c_void_p, C.POINTER(C.c_int),
                                         C.POINTER(C.c_int)]
        lib.XGetKeyboardMapping.restype = C.POINTER(C.c_ulong)
        lib.XGetKeyboardMapping.argtypes = [C.c_void_p, C.c_ubyte, C.c_int,
                                            C.POINTER(C.c_int)]
        lib.XChangeKeyboardMapping.argtypes = [C.c_void_p, C.c_int, C.c_int,
                                               C.POINTER(C.c_ulong), C.c_int]
        lib.XFree.argtypes = [C.c_void_p]
        lib.XStringToKeysym.restype = C.c_ulong
        lib.XStringToKeysym.argtypes = [C.c_char_p]
        lib.XKeysymToKeycode.restype = C.c_ubyte
        lib.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
        lib.XDefaultRootWindow.restype = C.c_ulong
        lib.XDefaultRootWindow.argtypes = [C.c_void_p]
        lib.XQueryPointer.restype = C.c_int
        lib.XQueryPointer.argtypes = [
            C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
            C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_int),
            C.POINTER(C.c_int), C.POINTER(C.c_uint)]
        lib.XInternAtom.restype = C.c_ulong
        lib.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
        lib.XGetWindowProperty.restype = C.c_int
        lib.XGetWindowProperty.argtypes = [
            C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long, C.c_int,
            C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
            C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
            C.POINTER(C.POINTER(C.c_ulong))]
        lib.XSync.argtypes = [C.c_void_p, C.c_int]
        lib.XFlush.argtypes = [C.c_void_p]
        xtst.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        display = lib.XOpenDisplay(None)
        if not display:
            self.error = "no X display"
            return
        self.lib = lib
        self.xtst = xtst
        self.display = display
        self.root = lib.XDefaultRootWindow(display)
        self.reload_keymap()

    @property
    def ok(self):
        return self.display is not None

    # -- keymap ------------------------------------------------------------
    def reload_keymap(self):
        """Index keysym -> (keycode, level) and find a free keycode."""
        lib = self.lib
        low, high = C.c_int(0), C.c_int(0)
        lib.XDisplayKeycodes(self.display, C.byref(low), C.byref(high))
        count = high.value - low.value + 1
        per = C.c_int(0)
        syms = lib.XGetKeyboardMapping(self.display, low.value, count,
                                       C.byref(per))
        self.min_keycode, self.max_keycode = low.value, high.value
        self.syms_per_code = per.value
        self.keymap = {}
        self.scratch_pool = []
        for index in range(count):
            keycode = low.value + index
            row = [syms[index * per.value + level] for level in range(per.value)]
            if not any(row):
                # Wholly unbound: borrowable for characters outside the layout.
                self.scratch_pool.append(keycode)
                continue
            # X treats a pair whose second entry is empty as (lower, upper)
            # when the first is alphabetic, so uppercase letters still work
            # on layouts that leave the shifted slot unfilled.
            if per.value > 1 and row[1] == NO_SYMBOL and 0x61 <= row[0] <= 0x7A:
                row[1] = row[0] - 0x20
            for level in (LEVEL_PLAIN, LEVEL_SHIFT):
                if level < len(row) and row[level] and row[level] not in self.keymap:
                    self.keymap[row[level]] = (keycode, level)
        lib.XFree(syms)
        self._bound = {}          # keycode -> keysym currently forced onto it

    def plan(self, text):
        """Text -> ([segment, ...], dropped).

        A segment is `(bindings, steps)`: the scratch keycodes it needs bound
        up front, and `(keycode, needs_shift)` pairs to fake. A new segment
        starts whenever the free keycodes run out, because rebinding one
        while keystrokes are still in flight corrupts them. Characters with
        no keycode and no keycode to borrow are dropped rather than typed as
        something else.
        """
        pool = self.scratch_pool
        segments = []
        dropped = []
        bindings = {}
        steps = []
        for char in text:
            keysym = keysym_for(char)
            found = self.keymap.get(keysym)
            if found is not None:
                keycode, level = found
                steps.append((keycode, level == LEVEL_SHIFT))
                continue
            if not pool:
                dropped.append(char)
                continue
            if keysym not in bindings:
                if len(bindings) == len(pool):
                    segments.append((bindings, steps))
                    bindings, steps = {}, []
                bindings[keysym] = pool[len(bindings)]
            steps.append((bindings[keysym], False))
        if steps:
            segments.append((bindings, steps))
        return segments, dropped

    # -- input state -------------------------------------------------------
    def held_modifiers(self):
        """Mask of modifiers physically down right now."""
        root_ret, child = C.c_ulong(), C.c_ulong()
        rx, ry, wx, wy = (C.c_int() for _ in range(4))
        mask = C.c_uint()
        self.lib.XQueryPointer(self.display, self.root, C.byref(root_ret),
                               C.byref(child), C.byref(rx), C.byref(ry),
                               C.byref(wx), C.byref(wy), C.byref(mask))
        # Shift, Control, Mod1 (Alt), Mod4 (Super) -- the ones that would
        # turn typed characters into shortcuts.
        return mask.value & (1 | (1 << 2) | (1 << 3) | (1 << 6))

    def active_window(self):
        """The window manager's active toplevel, or 0.

        `_NET_ACTIVE_WINDOW` is used rather than XGetInputFocus because it is
        the WM's stable notion of the focused toplevel; input focus can sit
        on a child window and move within one application.
        """
        atom = self.lib.XInternAtom(self.display, b"_NET_ACTIVE_WINDOW", True)
        if not atom:
            return 0
        actual_type, actual_format = C.c_ulong(), C.c_int()
        nitems, bytes_after = C.c_ulong(), C.c_ulong()
        data = C.POINTER(C.c_ulong)()
        status = self.lib.XGetWindowProperty(
            self.display, self.root, atom, 0, 1, False, XA_WINDOW,
            C.byref(actual_type), C.byref(actual_format), C.byref(nitems),
            C.byref(bytes_after), C.byref(data))
        if status != SUCCESS or not data or nitems.value < 1:
            return 0
        window = data[0]
        self.lib.XFree(data)
        return window

    # -- typing ------------------------------------------------------------
    def _fake(self, keycode, down):
        self.xtst.XTestFakeKeyEvent(self.display, keycode, down, 0)

    def _map_keycode(self, keycode, keysym):
        """Force every level of one keycode to `keysym` (0 to unbind)."""
        array = (C.c_ulong * self.syms_per_code)(*([keysym] * self.syms_per_code))
        self.lib.XChangeKeyboardMapping(self.display, keycode,
                                        self.syms_per_code, array, 1)

    def bind_scratch(self, bindings):
        for keysym, keycode in bindings.items():
            self._map_keycode(keycode, keysym)
            self._bound[keycode] = keysym
        self.lib.XSync(self.display, False)

    def release_scratch(self):
        """Hand every borrowed keycode back. Never call this with keystrokes
        still in flight -- see the note at the top of the module."""
        if not self._bound:
            return
        for keycode in list(self._bound):
            self._map_keycode(keycode, NO_SYMBOL)
        self._bound.clear()
        self.lib.XSync(self.display, False)

    def type_text(self, text, on_done=None, delay_ms=4, batch=6, settle_ms=40):
        """Type `text`, a few characters per main-loop tick.

        `on_done(typed, dropped)` fires once the last key has been sent and
        any borrowed keycodes have been handed back. `settle_ms` is the pause
        given to receiving clients to notice a keyboard remap, both before
        the first borrowed key is typed and before the keycode is released.
        """
        self.cancel()
        segments, dropped = self.plan(text)
        if not segments:
            if on_done:
                on_done(0, dropped)
            return None
        self._job = _TypeJob(self, segments, dropped, on_done, delay_ms,
                             batch, settle_ms)
        self._job.start()
        return self._job

    def cancel(self):
        if self._job is not None:
            self._job.cancel()
            self._job = None

    def shutdown(self):
        self.cancel()
        if self.ok:
            self.release_scratch()
            self.lib.XFlush(self.display)


class _TypeJob:
    """Types one segment at a time, a batch of keys per main-loop tick.

    The dock paints its own bar, so this must never block: a 200-character
    transcript typed in a loop at one key per 4ms would freeze the panel for
    most of a second. Each segment runs bind -> settle -> type -> settle, and
    the borrowed keycodes are handed back only after the final settle, once
    nothing is still in flight.
    """

    def __init__(self, typist, segments, dropped, on_done, delay_ms, batch,
                 settle_ms):
        self.typist = typist
        self.segments = segments
        self.dropped = dropped
        self.on_done = on_done
        self.delay_ms = max(1, int(delay_ms))
        self.batch = max(1, int(batch))
        self.settle_ms = max(1, int(settle_ms))
        self.segment = 0
        self.index = 0
        self.typed = 0
        self.source = 0
        self.shift_code = typist.lib.XKeysymToKeycode(
            typist.display, typist.lib.XStringToKeysym(b"Shift_L"))

    # -- scheduling --------------------------------------------------------
    def start(self):
        self._begin_segment()

    def _later(self, ms, func):
        self.source = GLib.timeout_add(ms, func)

    def _begin_segment(self):
        if self.segment >= len(self.segments):
            return self._finish()
        bindings, _steps = self.segments[self.segment]
        self.index = 0
        if bindings:
            self.typist.bind_scratch(bindings)
            self.typist.lib.XFlush(self.typist.display)
            # Give clients a moment to process the MappingNotify before a
            # borrowed keycode is faked, or they decode it with the old map.
            self._later(self.settle_ms, self._run_segment)
        else:
            self._later(self.delay_ms, self._run_segment)
        return GLib.SOURCE_REMOVE

    def _run_segment(self):
        typist = self.typist
        _bindings, steps = self.segments[self.segment]
        sent = 0
        while self.index < len(steps) and sent < self.batch:
            keycode, needs_shift = steps[self.index]
            if needs_shift:
                typist._fake(self.shift_code, True)
            typist._fake(keycode, True)
            typist._fake(keycode, False)
            if needs_shift:
                typist._fake(self.shift_code, False)
            self.index += 1
            self.typed += 1
            sent += 1
        typist.lib.XFlush(typist.display)
        if self.index < len(steps):
            return GLib.SOURCE_CONTINUE
        self.source = 0
        self.segment += 1
        # Let the keystrokes drain before anything touches the keymap again.
        self._later(self.settle_ms, self._end_segment)
        return GLib.SOURCE_REMOVE

    def _end_segment(self):
        self.source = 0
        self.typist.release_scratch()
        self.typist.lib.XFlush(self.typist.display)
        self._begin_segment()
        return GLib.SOURCE_REMOVE

    def _finish(self):
        self.source = 0
        if self.on_done:
            self.on_done(self.typed, self.dropped)
        return GLib.SOURCE_REMOVE

    def cancel(self):
        if self.source:
            GLib.source_remove(self.source)
            self.source = 0
        self.typist.release_scratch()
