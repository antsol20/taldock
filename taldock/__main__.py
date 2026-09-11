"""Entry point: python3 -m taldock"""
from __future__ import annotations

import os
import sys

from . import timing

# Keys XFCE binds to a bare Super press.
SUPER_KEYS = ("Super_L", "Super_R")
# Media keys, by keysym name, and the flag each one runs. xfce4-panel's
# pulseaudio plugin used to grab these; with the panel gone nothing did, so
# the keys reached no one at all.
MEDIA_KEYS = {
    "XF86AudioRaiseVolume": "--volume-up",
    "XF86AudioLowerVolume": "--volume-down",
    "XF86AudioMute": "--volume-mute",
    "XF86AudioMicMute": "--mic-mute",
}
SHORTCUT_CHANNEL = "xfce4-keyboard-shortcuts"

# Flags that do nothing but hand a command to the running dock. These are on
# a keypress path -- a held volume key repeats -- so they are answered before
# argparse is even imported, let alone gi. argparse alone costs ~25ms here.
CONTROL_FLAGS = {
    "--menu": "menu",
    "--volume-up": "volume-up",
    "--volume-down": "volume-down",
    "--volume-mute": "volume-mute",
    "--mic-mute": "mic-mute",
}


def send_control(command):
    from .control import send
    if send(command):
        return 0
    sys.stderr.write("taldock: not running\n")
    return 1


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
    import subprocess
    try:
        result = subprocess.run(["xfconf-query", "-c", SHORTCUT_CHANNEL] + list(args),
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _backup_path(name="super-binding.bak"):
    from gi.repository import GLib
    return os.path.join(GLib.get_user_config_dir(), "taldock", name)


def taldock_command(flag):
    """The command line XFCE should run for a shortcut."""
    import shutil
    installed = shutil.which("taldock")
    if installed:
        return f"{installed} {flag}"
    # Running from a source checkout: point at this interpreter and package.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"env PYTHONPATH={root} {sys.executable} -m taldock {flag}"


def menu_command():
    return taldock_command("--menu")


def _bind_keys(mapping, backup):
    """Point each xfconf shortcut at our command, saving what was there."""
    previous = {}
    for key, command in mapping.items():
        current = _xfconf("-p", f"/commands/custom/{key}")
        if current and current != command:
            previous[key] = current
        if _xfconf("-p", f"/commands/custom/{key}", "-n", "-t", "string",
                   "-s", command) is None:
            _xfconf("-p", f"/commands/custom/{key}", "-s", command)
    if previous:
        # Remember what was there so the matching --unbind can put it back.
        try:
            path = _backup_path(backup)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                for key, value in previous.items():
                    fh.write(f"{key}\t{value}\n")
        except OSError:
            pass


def _unbind_keys(keys, backup):
    restore = {}
    try:
        with open(_backup_path(backup), encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.rstrip("\n").partition("\t")
                if key and value:
                    restore[key] = value
    except OSError:
        pass
    for key in keys:
        value = restore.get(key)
        if value:
            _xfconf("-p", f"/commands/custom/{key}", "-s", value)
        else:
            _xfconf("-p", f"/commands/custom/{key}", "-r")


def bind_super():
    command = menu_command()
    _bind_keys({key: command for key in SUPER_KEYS}, "super-binding.bak")
    print(f"taldock: Super now opens the applications menu ({command})")
    return 0


def unbind_super():
    _unbind_keys(SUPER_KEYS, "super-binding.bak")
    print("taldock: Super key binding restored")
    return 0


def bind_media():
    _bind_keys({key: taldock_command(flag) for key, flag in MEDIA_KEYS.items()},
               "media-keys.bak")
    print("taldock: volume keys now drive the dock's mixer")
    return 0


def unbind_media():
    _unbind_keys(MEDIA_KEYS, "media-keys.bak")
    print("taldock: volume key bindings restored")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # Fast path: a bare control flag is answered without importing argparse.
    if len(args) == 1 and args[0] in CONTROL_FLAGS:
        timing.mark("entry (control flag)")
        return send_control(CONTROL_FLAGS[args[0]])

    import argparse
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
    parser.add_argument("--bind-media", action="store_true",
                        help="make the volume keys drive the dock's mixer")
    parser.add_argument("--unbind-media", action="store_true",
                        help="undo --bind-media")
    for flag in ("--volume-up", "--volume-down", "--volume-mute", "--mic-mute"):
        parser.add_argument(flag, action="store_true",
                            help=f"send {flag[2:]} to the running dock")
    args = parser.parse_args(argv)
    timing.mark("entry (interpreter + argparse)")

    for flag, command in CONTROL_FLAGS.items():
        if getattr(args, flag[2:].replace("-", "_")):
            return send_control(command)
    if args.version:
        from . import __version__
        print(f"taldock {__version__}")
        return 0
    if args.bind_super:
        return bind_super()
    if args.unbind_super:
        return unbind_super()
    if args.bind_media:
        return bind_media()
    if args.unbind_media:
        return unbind_media()
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
        import subprocess
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
