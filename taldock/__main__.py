"""Entry point: python3 -m taldock"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import timing

# Keys XFCE binds to a bare Super press.
SUPER_KEYS = ("Super_L", "Super_R")
SHORTCUT_CHANNEL = "xfce4-keyboard-shortcuts"


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


# --------------------------------------------------------------------------
# Super-key binding
#
# XFCE already binds a bare Super press through xfconf (that is how Whisker
# Menu does it), and xfwm4 arbitrates it against Super+<key> combos. Grabbing
# the key ourselves would swallow every other Super shortcut, so we reuse the
# desktop's own mechanism.
# --------------------------------------------------------------------------

def _xfconf(*args):
    try:
        result = subprocess.run(["xfconf-query", "-c", SHORTCUT_CHANNEL] + list(args),
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _backup_path():
    from gi.repository import GLib
    return os.path.join(GLib.get_user_config_dir(), "taldock", "super-binding.bak")


def menu_command():
    """The command line XFCE should run for a bare Super press."""
    installed = shutil.which("taldock")
    if installed:
        return f"{installed} --menu"
    # Running from a source checkout: point at this interpreter and package.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"env PYTHONPATH={root} {sys.executable} -m taldock --menu"


def bind_super():
    command = menu_command()
    previous = {}
    for key in SUPER_KEYS:
        current = _xfconf("-p", f"/commands/custom/{key}")
        if current and current != command:
            previous[key] = current
        if _xfconf("-p", f"/commands/custom/{key}", "-n", "-t", "string",
                   "-s", command) is None:
            _xfconf("-p", f"/commands/custom/{key}", "-s", command)
    if previous:
        # Remember what was there so --unbind-super can put it back.
        try:
            path = _backup_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                for key, value in previous.items():
                    fh.write(f"{key}\t{value}\n")
        except OSError:
            pass
    print(f"taldock: Super now opens the applications menu ({command})")
    return 0


def unbind_super():
    restore = {}
    try:
        with open(_backup_path(), encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.rstrip("\n").partition("\t")
                if key and value:
                    restore[key] = value
    except OSError:
        pass
    for key in SUPER_KEYS:
        value = restore.get(key)
        if value:
            _xfconf("-p", f"/commands/custom/{key}", "-s", value)
        else:
            _xfconf("-p", f"/commands/custom/{key}", "-r")
    print("taldock: Super key binding restored")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="taldock", description="A lightweight dock and panel for Xfce.")
    parser.add_argument("--version", action="store_true",
                        help="print version and exit")
    parser.add_argument("--replace", action="store_true",
                        help="stop xfce4-panel before starting")
    parser.add_argument("--config", metavar="PATH",
                        help="use an alternate config file")
    parser.add_argument("--menu", action="store_true",
                        help="open the applications menu on the running dock")
    parser.add_argument("--bind-super", action="store_true",
                        help="make the Super key open the applications menu")
    parser.add_argument("--unbind-super", action="store_true",
                        help="undo --bind-super")
    args = parser.parse_args(argv)
    timing.mark("entry (interpreter + argparse)")

    if args.menu:
        from .control import send
        if send("menu"):
            return 0
        sys.stderr.write("taldock: not running\n")
        return 1
    if args.version:
        from . import __version__
        print(f"taldock {__version__}")
        return 0
    if args.bind_super:
        return bind_super()
    if args.unbind_super:
        return unbind_super()
    if not preflight():
        return 1
    timing.mark("preflight (gi typelibs)")

    # A second dock would fight the first over struts, the tray watcher and
    # the control socket, so refuse rather than half-start.
    from .control import is_live
    if is_live():
        sys.stderr.write(
            "taldock: already running (use --menu to open the menu, "
            "or stop the existing one first)\n")
        return 1

    if args.replace:
        subprocess.run(["xfce4-panel", "--quit"], check=False,
                       stderr=subprocess.DEVNULL)

    import signal

    from gi.repository import GLib, Gtk
    from .dock import Dock
    timing.mark("taldock imports")

    dock = Dock(config_path=args.config)
    timing.mark("Dock() built")

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
