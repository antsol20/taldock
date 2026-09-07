"""Browser tab stacking.

A browser extension pushes its tab list to us over a unix socket (via a
native-messaging relay). Browsers do not expose their X11 window ids to
extensions, so browser windows are matched to X windows by comparing the
active tab's title against the X window title -- browsers put the active
tab's title there, which makes the mapping reliable in practice.

Everything here is optional: with no helper connected the dock simply falls
back to plain per-window entries.
"""
from __future__ import annotations

import base64
import binascii
import json
import os

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject

SOCKET_NAME = "tabs.sock"
# Suffixes browsers append to the active tab's title in the X window name.
_TITLE_SUFFIXES = (
    " - Google Chrome", " - Chromium", " - Brave", " - Microsoft Edge",
    " - Vivaldi", " — Mozilla Firefox", " - Mozilla Firefox",
    " - Chrome", " (Private Browsing)", " - Opera",
)


def socket_path():
    runtime = GLib.get_user_runtime_dir() or "/tmp"
    directory = os.path.join(runtime, "taldock")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    return os.path.join(directory, SOCKET_NAME)


def strip_browser_suffix(title):
    for suffix in _TITLE_SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    return title.strip()


class Tab:
    __slots__ = ("browser_key", "window_id", "id", "title", "url", "active",
                 "favicon_data", "index")

    def __init__(self, browser_key, window_id, data):
        self.browser_key = browser_key
        self.window_id = window_id
        self.id = data.get("id")
        self.title = data.get("title") or ""
        self.url = data.get("url") or ""
        self.active = bool(data.get("active"))
        self.index = data.get("index", 0)
        self.favicon_data = data.get("favicon") or ""


class TabRegistry(GObject.Object):
    """Receives tab lists from browser helpers and maps them to X windows."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.browsers = {}          # browser_key -> {window_id: [Tab, ...]}
        self._clients = {}          # connection -> browser_key
        self._favicons = {}         # data url -> pixbuf | None
        self.service = None
        self._start()

    # -- socket server -----------------------------------------------------
    def _start(self):
        path = socket_path()
        try:
            if os.path.exists(path):
                os.unlink(path)
            self.service = Gio.SocketService.new()
            address = Gio.UnixSocketAddress.new(path)
            self.service.add_address(address, Gio.SocketType.STREAM,
                                     Gio.SocketProtocol.DEFAULT, None)
            os.chmod(path, 0o600)
            self.service.connect("incoming", self._on_incoming)
            self.service.start()
        except (GLib.Error, OSError) as exc:
            print(f"taldock: tab helper socket unavailable: {exc}")
            self.service = None

    def _on_incoming(self, _service, connection, _source):
        stream = Gio.DataInputStream.new(connection.get_input_stream())
        stream.set_newline_type(Gio.DataStreamNewlineType.LF)
        self._clients[connection] = None
        self._read_line(stream, connection)
        return True

    def _read_line(self, stream, connection):
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None,
                               self._on_line, (stream, connection))

    def _on_line(self, stream, result, user_data):
        _stream, connection = user_data
        try:
            line, _length = stream.read_line_finish(result)
        except GLib.Error:
            line = None
        if not line:
            self._drop(connection)
            return
        try:
            text = line.decode("utf-8") if isinstance(line, bytes) else line
            self._handle(json.loads(text), connection)
        except (ValueError, UnicodeDecodeError):
            pass
        self._read_line(stream, connection)

    def _drop(self, connection):
        key = self._clients.pop(connection, None)
        if key:
            self.browsers.pop(key, None)
            self.emit("changed")
        try:
            connection.close()
        except GLib.Error:
            pass

    # -- protocol ----------------------------------------------------------
    def _handle(self, message, connection):
        if message.get("type") != "tabs":
            return
        key = message.get("browser") or "google-chrome.desktop"
        self._clients[connection] = key
        windows = {}
        for win in message.get("windows", []):
            win_id = win.get("id")
            tabs = [Tab(key, win_id, t) for t in win.get("tabs", [])]
            tabs.sort(key=lambda t: t.index)
            if tabs:
                windows[win_id] = tabs
        self.browsers[key] = windows
        self.emit("changed")

    def _send(self, browser_key, payload):
        for connection, key in self._clients.items():
            if key != browser_key:
                continue
            try:
                out = connection.get_output_stream()
                out.write_all((json.dumps(payload) + "\n").encode(), None)
                out.flush(None)
            except GLib.Error:
                pass
            return

    # -- window matching ---------------------------------------------------
    def for_key(self, app_key):
        """Map X window id -> tabs for the app group `app_key`."""
        windows = self.browsers.get(app_key)
        if not windows:
            return {}
        xwindows = self.model.windows_for(app_key)
        if not xwindows:
            return {}

        # Index browser windows by their active tab's title.
        by_title = {}
        for win_id, tabs in windows.items():
            active = next((t for t in tabs if t.active), tabs[0] if tabs else None)
            if active is not None:
                by_title.setdefault(active.title.strip(), []).append((win_id, tabs))

        result = {}
        unclaimed = dict(windows)
        for xwin in xwindows:
            title = strip_browser_suffix(xwin.get_name() or "")
            candidates = by_title.get(title)
            while candidates:
                win_id, tabs = candidates.pop(0)
                if win_id in unclaimed:
                    result[xwin.get_xid()] = tabs
                    del unclaimed[win_id]
                    break
        # A single leftover pair on each side is unambiguous, so pair them up.
        leftover_x = [w for w in xwindows if w.get_xid() not in result]
        if len(leftover_x) == 1 and len(unclaimed) == 1:
            result[leftover_x[0].get_xid()] = next(iter(unclaimed.values()))
        return result

    def available_for(self, app_key):
        return bool(self.browsers.get(app_key))

    # -- actions -----------------------------------------------------------
    def activate(self, tab):
        self._send(tab.browser_key,
                   {"type": "activate", "windowId": tab.window_id,
                    "tabId": tab.id})

    def close(self, tab):
        self._send(tab.browser_key, {"type": "close", "tabId": tab.id})

    # -- favicons ----------------------------------------------------------
    def favicon(self, tab, size=16):
        """Decode the extension-supplied data: URL into a pixbuf."""
        data = tab.favicon_data
        if not data or not data.startswith("data:"):
            return None
        if data in self._favicons:
            return self._favicons[data]
        pixbuf = None
        try:
            header, _, payload = data.partition(",")
            raw = (base64.b64decode(payload) if ";base64" in header
                   else GLib.uri_unescape_string(payload, None).encode())
            loader = GdkPixbuf.PixbufLoader()
            loader.set_size(size, size)
            loader.write(raw)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except (GLib.Error, ValueError, binascii.Error):
            pixbuf = None
        # Cache negatives too; a broken favicon should not be retried per frame.
        self._favicons[data] = pixbuf
        if len(self._favicons) > 400:
            self._favicons.clear()
        return pixbuf

    def shutdown(self):
        if self.service is not None:
            self.service.stop()
        try:
            os.unlink(socket_path())
        except OSError:
            pass
