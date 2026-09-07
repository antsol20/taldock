"""System tray: renders StatusNotifier items in a row."""
from __future__ import annotations

from gi.repository import Gtk

from ..dbusmenu import DBusMenu
from ..util import ICONS, paint_surface, rgba, rounded_rect, with_alpha
from .base import PanelItem

ICON = 17
SPACING = 5


class TrayItem(PanelItem):
    """A single dock cell that draws every tray icon side by side."""

    def setup(self):
        self.host = self.dock.tray_host
        self.host.connect("items-changed", self._on_items_changed)
        self._items = []
        self._hover_index = -1
        self._menus = {}

    def _on_items_changed(self, *_a):
        self.dock.invalidate_status_layout()

    def measure(self, height):
        self._items = self.host.visible_items()
        if not self._items:
            return 0
        return len(self._items) * ICON + (len(self._items) - 1) * SPACING + 12

    # -- painting ----------------------------------------------------------
    def draw(self, cr, w, h):
        if not self._items:
            return
        x = 6.0
        y = (h - ICON) / 2
        for index, item in enumerate(self._items):
            if index == self._hover_index:
                rgba(cr, self.theme["hover"])
                rounded_rect(cr, x - 3, y - 3, ICON + 6, ICON + 6, 7)
                cr.fill()
            surf = None
            name = item.effective_icon
            if name:
                # Symbolic first: themed tray art usually assumes a light
                # panel and vanishes on ours.
                surf = ICONS.symbolic_surface(name, ICON, self.dock.scale,
                                              self.theme["fg"])
                if surf is None:
                    surf = ICONS.surface(name, ICON, self.dock.scale,
                                         fallback=None)
            if surf is None and item.pixbuf is not None:
                surf = ICONS.surface_from_pixbuf(
                    item.pixbuf, ICON, self.dock.scale,
                    key=f"sni:{item.service}:{item.status}")
            if surf is None:
                surf = ICONS.surface("application-x-executable", ICON,
                                     self.dock.scale)
            paint_surface(cr, surf, x, y, ICON, self.dock.scale)

            if item.status == "NeedsAttention":
                rgba(cr, self.theme["crit"])
                cr.arc(x + ICON - 2, y + 2, 3.0, 0, 6.2832)
                cr.fill()
            x += ICON + SPACING

    # -- hit testing -------------------------------------------------------
    def _index_at(self, x):
        if not self._items:
            return -1
        rel = x - 6.0
        slot = ICON + SPACING
        index = int(rel // slot)
        if 0 <= index < len(self._items) and (rel - index * slot) <= ICON + 2:
            return index
        return -1

    def set_hover_x(self, x):
        index = self._index_at(x) if x is not None else -1
        if index != self._hover_index:
            self._hover_index = index
            self.redraw()

    def tooltip(self):
        if 0 <= self._hover_index < len(self._items):
            item = self._items[self._hover_index]
            return item.tooltip_text or item.title or item.id
        return None

    # -- input -------------------------------------------------------------
    def on_click(self, button, x, y, event):
        index = self._index_at(x)
        if index < 0:
            return False
        item = self._items[index]
        root_x, root_y = int(event.x_root), int(event.y_root)

        if button == 3 or (button == 1 and item.item_is_menu):
            if not self._show_menu(item, event):
                item.context_menu(root_x, root_y)
            return True
        if button == 1:
            # Many items implement no Activate and expect a left click to
            # open their menu; nm-applet is one. Fall back rather than
            # silently doing nothing.
            if not item.activate(root_x, root_y):
                if not self._show_menu(item, event):
                    item.context_menu(root_x, root_y)
            return True
        if button == 2:
            if not item.secondary_activate(root_x, root_y):
                item.activate(root_x, root_y)
            return True
        return False

    def on_scroll(self, direction, event):
        index = self._index_at(event.x - self.x)
        if index < 0:
            return False
        self._items[index].scroll(-15 if direction > 0 else 15, "vertical")
        return True

    def _show_menu(self, item, event):
        """Render the item's dbusmenu locally. False if it has none."""
        if not item.menu_path or not item.bus_name:
            return False
        menu = DBusMenu(item.bus_name, item.menu_path).build()
        if menu is None:
            return False
        self._menus[item.service] = menu    # keep alive while shown
        menu.attach_to_widget(self.dock.window, None)
        menu.connect("deactivate", lambda m: self._menus.pop(item.service, None))
        menu.popup_at_pointer(event)
        return True
