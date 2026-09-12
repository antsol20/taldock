#!/usr/bin/env python3
"""Round-trip test for taldock.xtype: type into our own window, read it back.

Deliberately self-contained -- it creates the window it types into, so it can
never leak synthetic keystrokes into whatever the user happens to be doing.
That has already happened once in this project by driving XTest at whatever
had focus.

    tools/type_selftest.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk       # noqa: E402

from taldock.xtype import Typist          # noqa: E402

CASES = [
    ("plain ascii", "Hello there, this is a test."),
    ("punctuation", "It's 42% done -- \"quoted\" (a/b) [x] {y} #1 @me ~ok $5 & 3<4>2 a|b\\c"),
    ("mixed case", "The Quick Brown Fox Jumps Over The Lazy Dog"),
    ("latin-1", "café naïve résumé £5 ½"),
    ("outside layout", "em—dash “smart” €10 50°"),
    ("newline", "first line\nsecond line"),
]

typist = Typist()
if not typist.ok:
    sys.exit(f"type_selftest: {typist.error}")

window = Gtk.Window(title="taldock type self-test")
window.set_default_size(560, 220)
view = Gtk.TextView()
view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
window.add(view)
window.show_all()
window.present()

results = []
queue = list(CASES)


def run_next():
    if not queue:
        return finish()
    name, text = queue.pop(0)
    buf = view.get_buffer()
    buf.set_text("")
    view.grab_focus()

    def done(_typed, dropped):
        got = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        results.append((name, text, got, dropped))
        GLib.timeout_add(60, run_next)

    typist.type_text(text, on_done=done, delay_ms=3, batch=8)
    return False


def finish():
    failures = 0
    for name, want, got, dropped in results:
        ok = want == got
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"      want {want!r}")
            print(f"      got  {got!r}")
        if dropped:
            print(f"      dropped {dropped!r}")
    print(f"\n{len(results) - failures}/{len(results)} cases passed")
    typist.shutdown()
    Gtk.main_quit()
    sys.exit(1 if failures else 0)


GLib.timeout_add(500, run_next)
Gtk.main()
