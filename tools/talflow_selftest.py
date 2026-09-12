#!/usr/bin/env python3
"""End-to-end check of the talflow pipeline, in a window this tool owns.

    tools/talflow_selftest.py [--shortcut '<Primary><Alt>space']

It creates its own focused text view and types into that, so it can never
leak a transcript into whatever the user is doing. Four things are checked:

  1. hold  -- an XTest hold of the shortcut records, stops on release and
              completes a real transcription round trip. With nobody
              speaking the expected result is "nothing heard", which still
              exercises pw-record, the WAV, the POST and the response.
  2. tap   -- a press shorter than `min_seconds` is discarded without
              spending a request.
  3. type  -- a transcript delivered while the target still has focus is
              typed into it verbatim.
  4. moved -- a transcript whose target has lost focus is NOT typed; it goes
              to the clipboard and the icon state says so.

The dock must not be running with the talflow widget at the same time: only
one X client can hold a given passive grab, and the second gets BadAccess.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ctypes as C                                    # noqa: E402
import gi                                             # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk              # noqa: E402

from taldock import talflow as tf                     # noqa: E402

SHORTCUT = None          # default: whatever the config file says
for index, arg in enumerate(sys.argv):
    if arg == "--shortcut" and index + 1 < len(sys.argv):
        SHORTCUT = sys.argv[index + 1]

x11 = C.CDLL("libX11.so.6")
xtst = C.CDLL("libXtst.so.6")
x11.XOpenDisplay.restype = C.c_void_p
x11.XOpenDisplay.argtypes = [C.c_char_p]
x11.XStringToKeysym.restype = C.c_ulong
x11.XStringToKeysym.argtypes = [C.c_char_p]
x11.XKeysymToKeycode.restype = C.c_ubyte
x11.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
x11.XFlush.argtypes = [C.c_void_p]
xtst.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
display = x11.XOpenDisplay(None)


def code(name):
    return x11.XKeysymToKeycode(display, x11.XStringToKeysym(name.encode()))


def chord_keycodes(accel):
    """Modifier keycodes for `accel`, then its own key, in press order."""
    keys = []
    for token, keysym in (("<Primary>", "Control_L"), ("<Control>", "Control_L"),
                          ("<Shift>", "Shift_L"), ("<Alt>", "Alt_L"),
                          ("<Super>", "Super_L")):
        if token in accel and code(keysym) not in keys:
            keys.append(code(keysym))
    main = accel.rsplit(">", 1)[-1] or accel
    return keys + [code(main)]



def chord(down):
    for keycode in (CHORD if down else reversed(CHORD)):
        xtst.XTestFakeKeyEvent(display, keycode, down, 0)
    x11.XFlush(display)


settings = tf.Settings()
if SHORTCUT:
    settings["shortcut"] = SHORTCUT
SHORTCUT = settings["shortcut"]
CHORD = chord_keycodes(SHORTCUT)
flow = tf.Talflow(settings=settings)
if not flow.hotkey.ok:
    sys.exit(f"talflow_selftest: cannot grab {SHORTCUT}: {flow.hotkey.error}\n"
             "Is a dock with the talflow widget already running?")

window = Gtk.Window(title="talflow self-test")
window.set_default_size(560, 200)
view = Gtk.TextView()
view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
window.add(view)
window.show_all()
window.present()

seen = []
flow.connect("state-changed", lambda *_a: seen.append(flow.state))
results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))


def buffer_text():
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


# -- 1. a real hold, all the way to a transcription ------------------------
def test_hold():
    del seen[:]
    view.grab_focus()
    chord(True)
    GLib.timeout_add(1500, lambda: (chord(False), False)[1])
    GLib.timeout_add(1700, check_recording_started)
    return False


def check_recording_started():
    record("hold: entered RECORDING", tf.RECORDING in seen, f"states={seen}")
    record("hold: entered SENDING", tf.SENDING in seen, f"states={seen}")
    GLib.timeout_add(200, wait_for_settle)
    return False


def wait_for_settle(waited=0):
    if flow.state == tf.SENDING and waited < 45000:
        GLib.timeout_add(250, wait_for_settle, waited + 250)
        return False
    ok = flow.state in (tf.IDLE, tf.PASTED, tf.CLIPBOARD)
    record("hold: round trip completed", ok,
           f"final={flow.state} message={flow.message!r} text={flow.last_text!r}")
    GLib.timeout_add(300, test_tap)
    return False


# -- 2. a tap is not worth a request ---------------------------------------
def test_tap():
    del seen[:]
    chord(True)
    GLib.timeout_add(80, lambda: (chord(False), False)[1])
    GLib.timeout_add(700, check_tap)
    return False


def check_tap():
    record("tap: discarded without sending", tf.SENDING not in seen,
           f"states={seen}")
    GLib.timeout_add(200, test_type)
    return False


# -- 3. focus unchanged: the transcript is typed ---------------------------
PHRASE = "Testing one two three, the quick brown fox."


def test_type():
    view.grab_focus()
    view.get_buffer().set_text("")
    flow._target = flow.typist.active_window()
    flow._on_transcribed(PHRASE, None)
    # Long enough for the keystrokes to land, but well inside the success
    # flash: PASTED reverts to IDLE after PASTED_HOLD_MS.
    GLib.timeout_add(700, check_type)
    return False


def check_type():
    got = buffer_text()
    record("type: delivered into the focused window", got == PHRASE,
           f"got {got!r}")
    record("type: flashes PASTED", flow.state == tf.PASTED,
           f"state={flow.state}")
    GLib.timeout_add(tf.PASTED_HOLD_MS + 300, check_type_reverts)
    return False


def check_type_reverts():
    record("type: flash reverts to IDLE", flow.state == tf.IDLE,
           f"state={flow.state}")
    GLib.timeout_add(300, test_moved)
    return False


# -- 4. focus moved: nothing typed, clipboard instead ----------------------
MOVED = "This must not be typed anywhere."


def test_moved():
    view.grab_focus()
    view.get_buffer().set_text("")
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("sentinel", -1)
    flow._target = 0xDEADBEEF          # a window that is certainly not focused
    flow._on_transcribed(MOVED, None)
    GLib.timeout_add(900, check_moved)
    return False


def check_moved():
    record("moved: nothing was typed", buffer_text() == "",
           f"buffer={buffer_text()!r}")
    record("moved: state is CLIPBOARD", flow.state == tf.CLIPBOARD,
           f"state={flow.state} message={flow.message!r}")
    clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
    record("moved: transcript on the clipboard", clip == MOVED, f"clip={clip!r}")
    GLib.timeout_add(200, finish)
    return False


def finish():
    failed = [name for name, ok, _d in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    flow.shutdown()
    Gtk.main_quit()
    sys.exit(1 if failed else 0)


print(f"talflow self-test: shortcut {flow.describe_shortcut()}, "
      f"model {settings['model']}")
GLib.timeout_add(600, test_hold)
Gtk.main()
