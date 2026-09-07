#!/usr/bin/env python3
"""Native-messaging relay between a browser extension and the taldock socket.

Chrome speaks a 4-byte-little-endian-length-prefixed JSON protocol on stdio.
The dock listens on a unix socket. This process just shuttles messages
between the two, in both directions.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading

SOCKET_NAME = "tabs.sock"


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "taldock", SOCKET_NAME)


def read_native():
    """Read one message from the browser, or None at end of stream."""
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    length = struct.unpack("<I", header)[0]
    if length == 0 or length > 64 * 1024 * 1024:
        return None
    payload = sys.stdin.buffer.read(length)
    if len(payload) < length:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except ValueError:
        return None


def write_native(message):
    data = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def pump_socket_to_browser(sock):
    """Forward dock -> browser commands (activate / close a tab)."""
    buffer = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except OSError:
            return
        if not chunk:
            return
        buffer += chunk
        while b"\n" in buffer:
            line, _, buffer = buffer.partition(b"\n")
            if not line.strip():
                continue
            try:
                write_native(json.loads(line.decode("utf-8")))
            except (ValueError, OSError):
                return


def main():
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(socket_path())
    except OSError:
        # The dock is not running. Exiting makes the extension back off and
        # retry later, which is exactly what we want.
        return 1

    reader = threading.Thread(target=pump_socket_to_browser, args=(sock,),
                              daemon=True)
    reader.start()

    while True:
        message = read_native()
        if message is None:
            break
        try:
            sock.sendall((json.dumps(message) + "\n").encode("utf-8"))
        except OSError:
            break
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
