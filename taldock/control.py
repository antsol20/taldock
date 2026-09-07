"""Client side of the control socket. Standard library only.

`taldock --menu` runs this path, so it must not import gi: pulling in GTK
would add a couple of hundred milliseconds to a keypress.
"""
from __future__ import annotations

import os
import socket

SOCKET_NAME = "control.sock"


def control_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "taldock", SOCKET_NAME)


def send(command, timeout=1.5):
    """Send one command to the running dock. True if it was acknowledged."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(control_path())
        sock.sendall((command + "\n").encode("utf-8"))
        reply = sock.recv(64).decode("utf-8", "replace").strip()
        sock.close()
        return reply == "ok"
    except OSError:
        return False
