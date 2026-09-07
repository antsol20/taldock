"""Control socket server: lets `taldock --menu` drive the running dock."""
from __future__ import annotations

import os

from gi.repository import Gio, GLib

from .control import control_path


class ControlServer:
    """Accepts one-line commands on a unix socket."""

    def __init__(self, handlers):
        self.handlers = handlers      # {"menu": callable, ...}
        self.service = None
        self._start()

    def _start(self):
        path = control_path()
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            if os.path.exists(path):
                os.unlink(path)
            self.service = Gio.SocketService.new()
            self.service.add_address(Gio.UnixSocketAddress.new(path),
                                     Gio.SocketType.STREAM,
                                     Gio.SocketProtocol.DEFAULT, None)
            os.chmod(path, 0o600)
            self.service.connect("incoming", self._on_incoming)
            self.service.start()
        except (GLib.Error, OSError) as exc:
            print(f"taldock: control socket unavailable: {exc}")
            self.service = None

    def _on_incoming(self, _service, connection, _source):
        stream = Gio.DataInputStream.new(connection.get_input_stream())
        stream.set_newline_type(Gio.DataStreamNewlineType.LF)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None,
                               self._on_line, connection)
        return True

    def _on_line(self, stream, result, connection):
        try:
            line, _length = stream.read_line_finish(result)
        except GLib.Error:
            line = None
        command = ""
        if line:
            command = (line.decode("utf-8", "replace") if isinstance(line, bytes)
                       else line).strip()
        handler = self.handlers.get(command)
        if handler is not None:
            # Run the action after replying, so the caller is not held up by
            # however long building a popup takes.
            GLib.idle_add(self._invoke, handler)
        self._reply(connection, "ok" if handler else "err")

    @staticmethod
    def _invoke(handler):
        handler()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _reply(connection, text):
        try:
            out = connection.get_output_stream()
            out.write_all((text + "\n").encode(), None)
            out.flush(None)
            connection.close()
        except GLib.Error:
            pass

    def shutdown(self):
        if self.service is not None:
            self.service.stop()
        try:
            os.unlink(control_path())
        except OSError:
            pass
