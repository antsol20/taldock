"""Window tracking and window->application matching.

Grouping windows under the right launcher is the part every dock gets
subtly wrong, so matching runs through an explicit cascade of strategies
(see AppDatabase.match) from most to least trustworthy.
"""
from __future__ import annotations

import os
import re

import gi

gi.require_version("Wnck", "3.0")
from gi.repository import Gio, GLib, GObject, Wnck  # noqa: E402

# Windows of these types never get a dock entry.
_SKIP_TYPES = {
    Wnck.WindowType.DESKTOP,
    Wnck.WindowType.DOCK,
    Wnck.WindowType.SPLASHSCREEN,
    Wnck.WindowType.MENU,
    Wnck.WindowType.TOOLBAR,
}

_EXEC_STRIP = re.compile(r"%[fFuUdDnNickvm]")
_WRAPPERS = {
    "env", "sh", "bash", "dbus-run-session", "flatpak", "snap", "gtk-launch",
    "python", "python3", "sudo", "pkexec", "nohup", "setsid",
}


def _exec_binary(appinfo):
    """Best-effort basename of the real binary a .desktop launches."""
    line = appinfo.get_commandline() or ""
    for token in _EXEC_STRIP.sub("", line).split():
        if "=" in token and not token.startswith("/"):
            continue          # leading VAR=value assignments
        if token.startswith("-"):
            continue
        base = os.path.basename(token)
        if base in _WRAPPERS:
            continue          # keep walking past the wrapper to the real cmd
        return base.lower()
    return ""


class AppDatabase(GObject.Object):
    """Index of installed .desktop files, queryable by window identity."""

    # Emitted after the index is rebuilt because applications were installed
    # or removed. The applications menu builds its rows once and keeps them,
    # so it has to be told: without this a newly installed application never
    # appeared in the menu until the dock was restarted.
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self):
        super().__init__()
        self.by_id = {}        # "firefox.desktop" -> DesktopAppInfo
        self._by_wmclass = {}  # StartupWMClass (lowered) -> DesktopAppInfo
        self._by_stem = {}     # desktop id stem -> DesktopAppInfo
        self._by_exec = {}     # exec basename -> DesktopAppInfo
        self._monitors = []
        self.reload()
        self._watch_dirs()

    # -- building ----------------------------------------------------------
    def reload(self):
        self.by_id.clear()
        self._by_wmclass.clear()
        self._by_stem.clear()
        self._by_exec.clear()
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            did = info.get_id()
            if not did:
                continue
            self.by_id[did] = info
            stem = did[:-8] if did.endswith(".desktop") else did
            # Reverse-DNS ids (org.gnome.Foo) also answer to their last part.
            for key in {stem.lower(), stem.rsplit(".", 1)[-1].lower()}:
                self._by_stem.setdefault(key, info)
            wmclass = info.get_startup_wm_class()
            if wmclass:
                self._by_wmclass.setdefault(wmclass.lower(), info)
            binary = _exec_binary(info)
            if binary:
                self._by_exec.setdefault(binary, info)

    def _watch_dirs(self):
        """Rebuild the index when applications are installed or removed."""
        dirs = [os.path.join(d, "applications") for d in
                [GLib.get_user_data_dir()] + list(GLib.get_system_data_dirs())]
        for path in dirs:
            gfile = Gio.File.new_for_path(path)
            try:
                mon = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error:
                continue
            mon.connect("changed", self._on_dir_changed)
            self._monitors.append(mon)

    def _on_dir_changed(self, *_a):
        if getattr(self, "_reload_source", 0):
            GLib.source_remove(self._reload_source)
        # Package installs touch many files; coalesce into one rebuild.
        self._reload_source = GLib.timeout_add_seconds(3, self._do_reload)

    def _do_reload(self):
        self._reload_source = 0
        self.reload()
        self.emit("changed")
        return GLib.SOURCE_REMOVE

    # -- querying ----------------------------------------------------------
    def get(self, desktop_id):
        info = self.by_id.get(desktop_id)
        if info is None and not desktop_id.endswith(".desktop"):
            info = self.by_id.get(desktop_id + ".desktop")
        if info is None:
            info = self._by_stem.get(desktop_id.replace(".desktop", "").lower())
        return info

    def match(self, window):
        """Resolve a Wnck.Window to a DesktopAppInfo, or None."""
        inst = (window.get_class_instance_name() or "").lower()
        cls = (window.get_class_group_name() or "").lower()

        # 1. StartupWMClass is the only field authored for exactly this job.
        for key in (cls, inst):
            if key and key in self._by_wmclass:
                return self._by_wmclass[key]
        # 2. Desktop id stem, which matches for the large majority of apps.
        for key in (inst, cls):
            if key and key in self._by_stem:
                return self._by_stem[key]
        # 3. The launched binary's name.
        for key in (inst, cls):
            if key and key in self._by_exec:
                return self._by_exec[key]
        # 4. Fall back to the running process's own executable name, which
        #    rescues apps that set no useful WM_CLASS at all.
        pid = window.get_pid()
        if pid:
            try:
                comm = os.path.basename(os.readlink(f"/proc/{pid}/exe")).lower()
            except OSError:
                comm = ""
            if comm and comm not in _WRAPPERS:
                return self._by_exec.get(comm) or self._by_stem.get(comm)
        return None


class WindowModel(GObject.Object):
    """Live view of open windows, grouped into stable application keys."""

    __gsignals__ = {
        # Something that affects what the dock draws has changed.
        "changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        # A window's title/icon changed; cheaper to handle separately.
        "window-updated": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self, appdb):
        super().__init__()
        self.appdb = appdb
        self.groups = {}          # key -> list[Wnck.Window], insertion ordered
        self.keys = {}            # Wnck.Window -> key
        self.appinfo = {}         # key -> DesktopAppInfo | None
        self._handlers = {}       # Wnck.Window -> [handler ids]
        self._emit_source = 0

        Wnck.set_client_type(Wnck.ClientType.PAGER)
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()
        self.screen.connect("window-opened", self._on_window_opened)
        self.screen.connect("window-closed", self._on_window_closed)
        for sig in ("active-window-changed", "active-workspace-changed",
                    "viewports-changed"):
            self.screen.connect(sig, self._on_screen_event)
        for win in self.screen.get_windows():
            self._track(win)

    # -- key derivation ----------------------------------------------------
    def key_for(self, window):
        info = self.appdb.match(window)
        if info is not None:
            return info.get_id(), info
        cls = (window.get_class_group_name()
               or window.get_class_instance_name()
               or window.get_name() or "unknown")
        return "wmclass:" + cls.lower(), None

    # -- tracking ----------------------------------------------------------
    def _relevant(self, window):
        return (window.get_window_type() not in _SKIP_TYPES
                and not window.is_skip_tasklist())

    def _track(self, window):
        if window in self.keys or not self._relevant(window):
            return
        key, info = self.key_for(window)
        self.keys[window] = key
        self.appinfo.setdefault(key, info)
        self.groups.setdefault(key, []).append(window)
        self._handlers[window] = [
            window.connect("name-changed", self._on_window_updated),
            window.connect("icon-changed", self._on_window_updated),
            window.connect("state-changed", self._on_window_state),
        ]

    def _untrack(self, window):
        key = self.keys.pop(window, None)
        for hid in self._handlers.pop(window, []):
            try:
                window.disconnect(hid)
            except TypeError:
                pass
        if key is None:
            return
        group = self.groups.get(key)
        if group and window in group:
            group.remove(window)
        if not group:
            self.groups.pop(key, None)
            self.appinfo.pop(key, None)

    # -- signal plumbing ---------------------------------------------------
    def _on_window_opened(self, _screen, window):
        self._track(window)
        self.queue_changed()

    def _on_window_closed(self, _screen, window):
        self._untrack(window)
        self.queue_changed()

    def _on_screen_event(self, _screen, *_a):
        self.queue_changed()

    def _on_window_state(self, window, *_a):
        # skip-tasklist can be toggled at runtime (e.g. Chrome's PWA windows).
        tracked = window in self.keys
        if tracked != self._relevant(window):
            self._untrack(window) if tracked else self._track(window)
            self.queue_changed()
        else:
            self.emit("window-updated", window)

    def _on_window_updated(self, window, *_a):
        self.emit("window-updated", window)

    def queue_changed(self):
        """Coalesce bursts of X events into a single redraw."""
        if self._emit_source:
            return
        self._emit_source = GLib.idle_add(self._flush, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _flush(self):
        self._emit_source = 0
        self.emit("changed")
        return GLib.SOURCE_REMOVE

    # -- queries -----------------------------------------------------------
    def windows_for(self, key):
        return list(self.groups.get(key, ()))

    def active_window(self):
        return self.screen.get_active_window()

    def is_active_group(self, key):
        win = self.screen.get_active_window()
        return win is not None and self.keys.get(win) == key

    def visible_windows(self, key):
        """Windows on the current workspace (or pinned to all)."""
        ws = self.screen.get_active_workspace()
        out = []
        for win in self.windows_for(key):
            if ws is None or win.is_pinned() or win.is_on_workspace(ws):
                out.append(win)
        return out

    # -- actions -----------------------------------------------------------
    @staticmethod
    def _stamp():
        return Gtk_get_current_event_time()

    def activate(self, window, timestamp=None):
        ts = timestamp or self._stamp()
        ws = window.get_workspace()
        if ws is not None and ws != self.screen.get_active_workspace():
            ws.activate(ts)
        if window.is_minimized():
            window.unminimize(ts)
        window.activate(ts)

    def toggle(self, window, timestamp=None):
        """Focus the window, or minimise it if it is already focused."""
        ts = timestamp or self._stamp()
        if window.is_active() and not window.is_minimized():
            window.minimize()
        else:
            self.activate(window, ts)

    def cycle(self, key, timestamp=None):
        """Focus the next window of a group, wrapping around."""
        wins = self.visible_windows(key) or self.windows_for(key)
        if not wins:
            return
        ts = timestamp or self._stamp()
        active = self.screen.get_active_window()
        if len(wins) == 1:
            self.toggle(wins[0], ts)
            return
        if active in wins:
            nxt = wins[(wins.index(active) + 1) % len(wins)]
        else:
            # Prefer restoring something minimised over re-raising the top one.
            nxt = next((w for w in wins if w.is_minimized()), wins[0])
        self.activate(nxt, ts)

    def minimize_all(self, key):
        for win in self.visible_windows(key):
            if not win.is_minimized():
                win.minimize()

    def close_all(self, key, timestamp=None):
        ts = timestamp or self._stamp()
        for win in self.windows_for(key):
            win.close(ts)


def Gtk_get_current_event_time():
    """Timestamp for focus requests; X refuses stale ones."""
    from gi.repository import Gtk
    ts = Gtk.get_current_event_time()
    return ts if ts else GLib.get_monotonic_time() // 1000
