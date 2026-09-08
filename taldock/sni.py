"""StatusNotifierItem tray: watcher, host and item model.

We publish org.kde.StatusNotifierWatcher ourselves (the panel we replace
normally owns it) but also work as a plain host when some other watcher is
already running, so the dock can be tested next to an existing panel.
"""
from __future__ import annotations

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACES = ("org.kde.StatusNotifierItem", "org.ayatana.NotificationItem")
REAP_INTERVAL_S = 180

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem">
      <arg type="s" direction="in" name="service"/>
    </method>
    <method name="RegisterStatusNotifierHost">
      <arg type="s" direction="in" name="service"/>
    </method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
    <signal name="StatusNotifierHostUnregistered"/>
  </interface>
</node>
"""


def split_service(service):
    """':1.43/org/path' or ':1.43' -> (bus_name, object_path)."""
    if service.startswith("/"):
        return None, service
    idx = service.find("/")
    if idx == -1:
        return service, "/StatusNotifierItem"
    return service[:idx], service[idx:]


def pixmap_to_pixbuf(pixmaps):
    """Convert SNI's a(iiay) ARGB32 big-endian pixmaps to a pixbuf.

    Picks the largest variant; the tray downscales, which beats upscaling a
    small one.
    """
    best = None
    for width, height, data in pixmaps or ():
        if width <= 0 or height <= 0 or len(data) < width * height * 4:
            continue
        if best is None or width * height > best[0] * best[1]:
            best = (width, height, data)
    if best is None:
        return None
    width, height, data = best
    # ARGB (network order) -> RGBA, which is what GdkPixbuf expects.
    src = bytearray(data)
    out = bytearray(len(src))
    out[0::4] = src[1::4]
    out[1::4] = src[2::4]
    out[2::4] = src[3::4]
    out[3::4] = src[0::4]
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(out)), GdkPixbuf.Colorspace.RGB, True, 8,
        width, height, width * 4)


class TrayItem(GObject.Object):
    """One tray icon, mirroring a remote StatusNotifierItem."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ()),
                    "gone": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.bus_name, self.path = split_service(service)
        self.proxy = None
        self.iface = None
        self.id = ""
        self.title = ""
        self.status = "Active"
        self.icon_name = ""
        self.icon_theme_path = ""
        self.attention_icon_name = ""
        self.pixbuf = None
        self.tooltip_text = ""
        self.menu_path = None
        self.item_is_menu = False
        self._props = {}
        self._connect()

    # -- dbus --------------------------------------------------------------
    def _connect(self):
        for iface in ITEM_IFACES:
            try:
                proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                    self.bus_name, self.path, iface, None)
            except GLib.Error:
                continue
            # An interface the peer does not implement yields no properties.
            if proxy.get_cached_property_names():
                self.proxy = proxy
                self.iface = iface
                break
        if self.proxy is None:
            return
        self.proxy.connect("g-properties-changed", self._on_props_changed)
        self.proxy.connect("g-signal", self._on_signal)
        self._fetch_all()
        self.refresh()

    def _on_signal(self, _proxy, _sender, signal, _params):
        if signal.startswith("New"):
            # Ayatana emits New* without a matching PropertiesChanged, so the
            # proxy cache is stale. One GetAll beats a burst of per-property
            # Gets: these signals fire often (every signal-strength change).
            self._fetch_all()
            self.refresh()

    def _fetch_all(self):
        try:
            res = self.proxy.call_sync(
                "org.freedesktop.DBus.Properties.GetAll",
                GLib.Variant("(s)", (self.iface,)),
                Gio.DBusCallFlags.NONE, 2000, None)
            self._props = res.unpack()[0]
        except GLib.Error:
            self._props = {}

    def _prop(self, name, default=None):
        if name in self._props:
            return self._props[name]
        val = self.proxy.get_cached_property(name)
        if val is not None:
            return val.unpack()
        return default

    def _on_props_changed(self, _proxy, changed, _invalidated):
        self._props.update(changed.unpack())
        self.refresh()

    def refresh(self):
        if self.proxy is None:
            return
        self.id = self._prop("Id", "") or ""
        self.title = self._prop("Title", "") or ""
        self.status = self._prop("Status", "Active") or "Active"
        self.icon_theme_path = self._prop("IconThemePath", "") or ""
        self.icon_name = self._prop("IconName", "") or ""
        self.attention_icon_name = self._prop("AttentionIconName", "") or ""
        self.item_is_menu = bool(self._prop("ItemIsMenu", False))
        menu = self._prop("Menu")
        self.menu_path = menu if menu and menu != "/" else None

        tooltip = self._prop("ToolTip")
        if isinstance(tooltip, tuple) and len(tooltip) >= 4:
            self.tooltip_text = tooltip[2] or tooltip[3] or ""
        else:
            self.tooltip_text = self.title

        if self.icon_theme_path:
            theme = Gtk.IconTheme.get_default()
            if self.icon_theme_path not in (theme.get_search_path() or []):
                theme.append_search_path(self.icon_theme_path)

        name = (self.attention_icon_name if self.status == "NeedsAttention"
                and self.attention_icon_name else self.icon_name)
        if not name:
            key = ("AttentionIconPixmap" if self.status == "NeedsAttention"
                   else "IconPixmap")
            self.pixbuf = pixmap_to_pixbuf(self._prop(key) or self._prop("IconPixmap"))
        else:
            self.pixbuf = None
        self.emit("changed")

    # -- actions -----------------------------------------------------------
    def _call(self, method, params):
        """Invoke a method on the item. False if it is not implemented.

        Plenty of Ayatana items (nm-applet among them) implement no Activate
        at all and expect a left click to open their menu, so callers need to
        know whether the call actually landed.

        A failure may also mean the item is a corpse -- see is_alive() -- so
        check before reporting "not implemented" and letting the caller fall
        back to a menu that is equally gone.
        """
        if self.proxy is None:
            return False
        try:
            self.proxy.call_sync(method, params, Gio.DBusCallFlags.NONE,
                                 2000, None)
            return True
        except GLib.Error:
            if not self.is_alive():
                self.emit("gone")
            return False

    @staticmethod
    def _is_dead_error(exc):
        """True when the peer answered "that object does not exist".

        A timeout means busy, not dead: an item that is merely slow must not
        be thrown away.
        """
        return (exc.matches(Gio.dbus_error_quark(),
                            Gio.DBusError.UNKNOWN_METHOD)
                or exc.matches(Gio.dbus_error_quark(),
                               Gio.DBusError.UNKNOWN_OBJECT)
                or exc.matches(Gio.dbus_error_quark(),
                               Gio.DBusError.SERVICE_UNKNOWN)
                or "Object destroyed" in (exc.message or ""))

    def probe(self):
        """Asynchronously check the object still exists; emit "gone" if not.

        Must not be the synchronous version: this runs on a timer with no
        user waiting for it, and a hung tray application would otherwise
        stall the whole bar for the call timeout.
        """
        if self.proxy is None:
            self.emit("gone")
            return
        self.proxy.call("org.freedesktop.DBus.Properties.GetAll",
                        GLib.Variant("(s)", (self.iface,)),
                        Gio.DBusCallFlags.NONE, 5000, None,
                        self._on_probe_done, None)

    def _on_probe_done(self, proxy, result, _data):
        try:
            proxy.call_finish(result)
        except GLib.Error as exc:
            if self._is_dead_error(exc):
                self.emit("gone")

    def is_alive(self):
        """False once the remote object has been torn down.

        An application is meant to drop its bus name or call
        UnregisterStatusNotifierItem when it destroys its tray icon. Some do
        neither: the Claude desktop app destroys the object but keeps the
        connection open, so the name still exists, nothing is signalled, and
        the last icon we fetched would sit in the tray for ever doing
        nothing when clicked. The only way to find out is to ask.

        The bus answers for a destroyed object with UnknownMethod
        ("Method is no longer available") or Failed ("Object destroyed"),
        both of which come from the peer, so a live item never trips this.
        """
        if self.proxy is None:
            return False
        try:
            self.proxy.call_sync(
                "org.freedesktop.DBus.Properties.GetAll",
                GLib.Variant("(s)", (self.iface,)),
                Gio.DBusCallFlags.NONE, 2000, None)
            return True
        except GLib.Error as exc:
            return not self._is_dead_error(exc)

    def activate(self, x, y):
        return self._call("Activate", GLib.Variant("(ii)", (int(x), int(y))))

    def secondary_activate(self, x, y):
        return self._call("SecondaryActivate",
                          GLib.Variant("(ii)", (int(x), int(y))))

    def context_menu(self, x, y):
        return self._call("ContextMenu", GLib.Variant("(ii)", (int(x), int(y))))

    def scroll(self, delta, orientation="vertical"):
        return self._call("Scroll",
                          GLib.Variant("(is)", (int(delta), orientation)))

    @property
    def effective_icon(self):
        """Icon name to render, or None when a pixmap should be used."""
        if self.status == "NeedsAttention" and self.attention_icon_name:
            return self.attention_icon_name
        return self.icon_name or None


class StatusNotifierHost(GObject.Object):
    """Owns the watcher when it can, and always hosts the items."""

    __gsignals__ = {"items-changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self):
        super().__init__()
        self.items = {}             # service -> TrayItem
        self._owning_watcher = False
        self._registered_hosts = []
        self._reg_id = 0
        self._name_ids = []
        self._watcher_proxy = None

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.host_name = f"org.kde.StatusNotifierHost-{Gio.dbus_generate_guid()[:8]}"
        self._own_watcher()
        self._own_host_name()
        self._watch_watcher()
        self._reap_id = GLib.timeout_add_seconds(REAP_INTERVAL_S, self._reap)

    # -- watcher side ------------------------------------------------------
    def _own_watcher(self):
        node = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML)
        self._iface_info = node.interfaces[0]
        self._name_ids.append(Gio.bus_own_name(
            Gio.BusType.SESSION, WATCHER_NAME,
            Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE,
            self._on_watcher_bus_acquired,
            self._on_watcher_acquired,
            self._on_watcher_lost))

    def _on_watcher_bus_acquired(self, conn, _name):
        try:
            self._reg_id = conn.register_object(
                WATCHER_PATH, self._iface_info,
                self._watcher_method, self._watcher_get_prop, None)
        except GLib.Error as exc:
            print(f"taldock: could not export watcher: {exc}")

    def _on_watcher_acquired(self, _conn, _name):
        self._owning_watcher = True
        self._emit_watcher_signal("StatusNotifierHostRegistered", None)

    def _on_watcher_lost(self, _conn, _name):
        self._owning_watcher = False

    def _watcher_method(self, _conn, sender, _path, _iface, method, params,
                        invocation):
        if method == "RegisterStatusNotifierItem":
            service = params.unpack()[0]
            # Bare paths are relative to the caller's unique bus name.
            if service.startswith("/"):
                service = sender + service
            # Reply BEFORE touching the item. Building its proxy queries the
            # caller synchronously, and the caller may still be blocked
            # waiting for this very reply -- which loses the item entirely.
            invocation.return_value(None)
            GLib.idle_add(self._register_item, service)
        elif method == "RegisterStatusNotifierHost":
            host = params.unpack()[0]
            if host not in self._registered_hosts:
                self._registered_hosts.append(host)
            invocation.return_value(None)
            self._emit_watcher_signal("StatusNotifierHostRegistered", None)
        else:
            invocation.return_value(None)

    def _register_item(self, service):
        self.add_item(service)
        self._emit_watcher_signal("StatusNotifierItemRegistered", service)
        return GLib.SOURCE_REMOVE

    def _watcher_get_prop(self, _conn, _sender, _path, _iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self.items.keys()))
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def _emit_watcher_signal(self, name, arg):
        if not self._owning_watcher:
            return
        body = GLib.Variant("(s)", (arg,)) if arg is not None else None
        try:
            self.bus.emit_signal(None, WATCHER_PATH, WATCHER_NAME, name, body)
        except GLib.Error:
            pass

    # -- host side ---------------------------------------------------------
    def _own_host_name(self):
        self._name_ids.append(Gio.bus_own_name(
            Gio.BusType.SESSION, self.host_name,
            Gio.BusNameOwnerFlags.NONE, None, None, None))

    def _watch_watcher(self):
        """Track whichever watcher is live and mirror its item list."""
        Gio.bus_watch_name(
            Gio.BusType.SESSION, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared, self._on_watcher_vanished)

    def _on_watcher_appeared(self, _conn, _name, owner):
        # When we are the watcher, talking to it means talking to ourselves.
        # Any synchronous call here would block the very main loop that has
        # to answer it, so short-circuit: items register straight into
        # self.items through _watcher_method.
        if owner and owner == self.bus.get_unique_name():
            return
        # A foreign watcher (another panel) is in charge. Everything below is
        # asynchronous for the same deadlock-avoidance reason.
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            WATCHER_NAME, WATCHER_PATH, WATCHER_NAME, None,
            self._on_watcher_proxy_ready, None)

    def _on_watcher_proxy_ready(self, _source, result, _data):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return
        self._watcher_proxy = proxy
        proxy.connect("g-signal", self._on_watcher_signal)
        proxy.call("RegisterStatusNotifierHost",
                   GLib.Variant("(s)", (self.host_name,)),
                   Gio.DBusCallFlags.NONE, 2000, None, None, None)
        existing = proxy.get_cached_property("RegisteredStatusNotifierItems")
        for service in (existing.unpack() if existing else []):
            self.add_item(service)

    def _on_watcher_vanished(self, _conn, _name):
        self._watcher_proxy = None

    def _on_watcher_signal(self, _proxy, _sender, signal, params):
        if signal == "StatusNotifierItemRegistered":
            self.add_item(params.unpack()[0])
        elif signal == "StatusNotifierItemUnregistered":
            self.remove_item(params.unpack()[0])

    # -- item bookkeeping --------------------------------------------------
    def add_item(self, service):
        if service in self.items:
            return
        item = TrayItem(service)
        if item.proxy is None:
            return
        self.items[service] = item
        item.connect("changed", lambda *_a: self.emit("items-changed"))
        item.connect("gone", lambda *_a, s=service: self.remove_item(s))
        # Drop the icon when its owner leaves the bus.
        if item.bus_name:
            item._watch_id = Gio.bus_watch_name(
                Gio.BusType.SESSION, item.bus_name,
                Gio.BusNameWatcherFlags.NONE, None,
                lambda *_a, s=service: self.remove_item(s))
        self.emit("items-changed")

    def remove_item(self, service):
        item = self.items.pop(service, None)
        if item is None:
            return
        watch = getattr(item, "_watch_id", 0)
        if watch:
            Gio.bus_unwatch_name(watch)
        self._emit_watcher_signal("StatusNotifierItemUnregistered", service)
        self.emit("items-changed")

    def _reap(self):
        """Drop items whose remote object has been destroyed.

        The one piece of polling in the dock, and it is here because there is
        nothing to push: an application that tears down its tray object
        without dropping its bus name emits no signal at all, so a dead icon
        would otherwise stay in the tray until something clicked it. One
        D-Bus round trip per item every few minutes is cheaper than that.
        """
        for item in list(self.items.values()):
            item.probe()            # async; removes itself via "gone"
        return GLib.SOURCE_CONTINUE

    def visible_items(self):
        return [i for i in self.items.values() if i.status != "Passive"]
