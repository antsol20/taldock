#!/usr/bin/env python3
"""XTest pointer/keyboard driver, for looking at the dock under real input.

    tools/drive.py move:900,1047 click:1 wait:0.5 key:Escape type:hello
    tools/drive.py key:XF86AudioRaiseVolume

Keys are X keysym names, so the media keys can be fired exactly as the
hardware sends them. Pair it with tools/cap.py, which captures without
perturbing the pointer.
"""
import ctypes as C
import sys
import time

x11 = C.CDLL("libX11.so.6")
xtst = C.CDLL("libXtst.so.6")
x11.XOpenDisplay.restype = C.c_void_p
x11.XOpenDisplay.argtypes = [C.c_char_p]
x11.XFlush.argtypes = [C.c_void_p]
x11.XStringToKeysym.restype = C.c_ulong
x11.XStringToKeysym.argtypes = [C.c_char_p]
x11.XKeysymToKeycode.restype = C.c_ubyte
x11.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
xtst.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
xtst.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
xtst.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]

display = x11.XOpenDisplay(None)
if not display:
    sys.exit("drive: no X display")

NAMED = {" ": "space", ".": "period", "-": "minus", "_": "underscore",
         "/": "slash", ":": "colon", "@": "at"}


def flush(pause=0.12):
    x11.XFlush(display)
    time.sleep(pause)


def key(name):
    keysym = x11.XStringToKeysym(name.encode())
    if not keysym:
        sys.exit(f"drive: unknown keysym {name!r}")
    code = x11.XKeysymToKeycode(display, keysym)
    if not code:
        sys.exit(f"drive: {name!r} is not in the current keymap")
    xtst.XTestFakeKeyEvent(display, code, 1, 0)
    xtst.XTestFakeKeyEvent(display, code, 0, 0)
    flush(0.05)


for arg in sys.argv[1:]:
    verb, _, value = arg.partition(":")
    if verb == "move":
        x, y = (int(v) for v in value.split(","))
        xtst.XTestFakeMotionEvent(display, -1, x, y, 0)
        flush()
    elif verb == "click":
        button = int(value or 1)
        xtst.XTestFakeButtonEvent(display, button, 1, 0)
        xtst.XTestFakeButtonEvent(display, button, 0, 0)
        flush(0.35)
    elif verb == "key":
        key(value)
    elif verb == "type":
        for ch in value:
            key(NAMED.get(ch, ch))
    elif verb == "wait":
        time.sleep(float(value))
    else:
        sys.exit(f"drive: unknown verb {verb!r}")
