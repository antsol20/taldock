"""com.canonical.dbusmenu -> Gtk.Menu.

Tray items publish their menus as a remote tree; this walks that tree and
builds a real GtkMenu, forwarding clicks back over the bus.
"""
from __future__ import annotations

from gi.repository import Gio, GLib, Gtk

IFACE = "com.canonical.dbusmenu"


class DBusMenu:
    """Builds (and rebuilds) a Gtk.Menu from a remote dbusmenu."""

    def __init__(self, bus_name, path):
        self.bus_name = bus_name
        self.path = path
        self.proxy = None
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                bus_name, path, IFACE, None)
        except GLib.Error:
            self.proxy = None

    # -- remote calls ------------------------------------------------------
    def _layout(self, parent_id=0, depth=-1):
        if self.proxy is None:
            return None
        try:
            res = self.proxy.call_sync(
                "GetLayout",
                GLib.Variant("(iias)", (parent_id, depth, [])),
                Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            return None
        return res.unpack()[1]     # (id, props, children)

    def _about_to_show(self, item_id):
        if self.proxy is None:
            return
        try:
            self.proxy.call_sync("AboutToShow", GLib.Variant("(i)", (item_id,)),
                                 Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass

    def _event(self, item_id, event="clicked"):
        if self.proxy is None:
            return
        try:
            self.proxy.call_sync(
                "Event",
                GLib.Variant("(isvu)", (item_id, event,
                                        GLib.Variant("i", 0),
                                        int(GLib.get_real_time() // 1000000))),
                Gio.DBusCallFlags.NO_AUTO_START, 3000, None)
        except GLib.Error:
            pass

    # -- building ----------------------------------------------------------
    def build(self):
        """Return a populated Gtk.Menu, or None if the menu is unavailable."""
        self._about_to_show(0)
        root = self._layout()
        if root is None:
            return None
        menu = Gtk.Menu()
        self._fill(menu, root[2])
        if not menu.get_children():
            return None
        menu.show_all()
        return menu

    def _fill(self, menu, children):
        for child in children:
            item = self._make_item(child)
            if item is not None:
                menu.append(item)

    def _make_item(self, node):
        item_id, props, children = node
        if not props.get("visible", True):
            return None

        kind = props.get("type", "standard")
        if kind == "separator":
            return Gtk.SeparatorMenuItem()

        # dbusmenu marks mnemonics with a single underscore and escapes a
        # literal one as "__"; GtkMenuItem here renders plain text.
        label = (props.get("label") or "").replace("__", "\0")
        label = label.replace("_", "").replace("\0", "_")

        toggle_type = props.get("toggle-type", "")
        if toggle_type == "checkmark":
            widget = Gtk.CheckMenuItem(label=label)
            widget.set_active(props.get("toggle-state", 0) == 1)
        elif toggle_type == "radio":
            widget = Gtk.CheckMenuItem(label=label)
            widget.set_draw_as_radio(True)
            widget.set_active(props.get("toggle-state", 0) == 1)
        else:
            widget = Gtk.MenuItem(label=label)

        widget.set_sensitive(props.get("enabled", True))

        if props.get("children-display") == "submenu" or children:
            submenu = Gtk.Menu()
            # Ask the app to populate lazily, then read the real subtree.
            self._about_to_show(item_id)
            node2 = self._layout(item_id)
            self._fill(submenu, node2[2] if node2 else children)
            if submenu.get_children():
                widget.set_submenu(submenu)
        else:
            widget.connect("activate", self._on_activate, item_id, toggle_type)
        return widget

    def _on_activate(self, _widget, item_id, toggle_type):
        # Toggle items report their own state back over the bus; the local
        # widget state is only a hint until the app confirms it.
        self._event(item_id, "clicked")
