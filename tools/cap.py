#!/usr/bin/env python3
"""Screen capture that does not disturb the pointer.

    tools/cap.py out.png            # whole screen
    tools/cap.py out.png 0 1000 1920 80

Use this rather than xfce4-screenshooter: the screenshooter moves the
pointer, so hover states and launcher magnification collapse before the
frame is taken.
"""
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk  # noqa: E402

if len(sys.argv) < 2:
    sys.exit(__doc__)

root = Gdk.get_default_root_window()
x, y, w, h = 0, 0, root.get_width(), root.get_height()
if len(sys.argv) >= 6:
    x, y, w, h = (int(v) for v in sys.argv[2:6])
pixbuf = Gdk.pixbuf_get_from_window(root, x, y, w, h)
if pixbuf is None:
    sys.exit("cap: nothing captured (is the screen blanked?)")
pixbuf.savev(sys.argv[1], "png", [], [])
print(f"{sys.argv[1]}  {w}x{h}+{x}+{y}")
