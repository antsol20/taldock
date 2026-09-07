"""Entry point: python3 -m taldock"""
from __future__ import annotations

import argparse
import os
import signal
import sys


def preflight():
    """Fail with an actionable message rather than a traceback."""
    missing = []
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Wnck", "3.0")
        from gi.repository import Gtk, Wnck  # noqa: F401
    except (ImportError, ValueError):
        missing.append("gir1.2-wnck-3.0 python3-gi")
    try:
        import cairo  # noqa: F401
    except ImportError:
        missing.append("python3-gi-cairo")
    if missing:
        sys.stderr.write(
            "taldock: missing dependencies.\n"
            "  sudo apt install -y " + " ".join(missing) + "\n")
        return False
    if not os.environ.get("DISPLAY"):
        sys.stderr.write("taldock: no DISPLAY; this dock targets X11.\n")
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="taldock", description="A lightweight dock and panel for Xfce.")
    parser.add_argument("--version", action="store_true",
                        help="print version and exit")
    parser.add_argument("--replace", action="store_true",
                        help="stop xfce4-panel before starting")
    parser.add_argument("--config", metavar="PATH",
                        help="use an alternate config file")
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__
        print(f"taldock {__version__}")
        return 0
    if not preflight():
        return 1

    if args.replace:
        import subprocess
        subprocess.run(["xfce4-panel", "--quit"], check=False,
                       stderr=subprocess.DEVNULL)

    from gi.repository import GLib, Gtk
    from .dock import Dock

    dock = Dock(config_path=args.config)

    def stop(*_a):
        dock.shutdown()
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    try:
        dock.run()
    finally:
        dock.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
