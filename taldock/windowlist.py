"""Hover popup listing an app's windows, and its browser tabs when known."""
from __future__ import annotations

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from .popup import Popup, css_provider, label, separator
from .util import ICONS

ROW_ICON = 16
MAX_ROWS = 14


class WindowListPopup(Popup):
    """Window (and tab) switcher for a single dock icon."""

    def __init__(self, dock, icon, sticky=False):
        # Hover previews must not grab; a click-opened list should.
        super().__init__(dock, padding=8, grab=sticky)
        self.dock = dock
        self.icon = icon
        self.model = dock.windows
        self.sticky = sticky

        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), css_provider(self.theme),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.content.get_style_context().add_class("td-popup")
        self.content.set_size_request(268, -1)

        header = Gtk.Box(spacing=8)
        header.set_margin_start(4)
        header.set_margin_bottom(2)
        header.pack_start(label(icon.name, "td-title"), True, True, 0)
        count = icon.window_count()
        if count:
            header.pack_end(label(f"{count}", "td-dim", xalign=1.0), False, False, 0)
        self.content.pack_start(header, False, False, 0)

        self._build_rows()

        if not sticky:
            # Hovering the list keeps it open; leaving closes it.
            self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK
                            | Gdk.EventMask.LEAVE_NOTIFY_MASK)
            self.connect("enter-notify-event", self._on_enter)
            self.connect("leave-notify-event", self._on_leave)

    # -- rows --------------------------------------------------------------
    def _build_rows(self):
        windows = self.model.windows_for(self.icon.key)
        tabs_by_window = self.dock.tabs.for_key(self.icon.key) if self.dock.tabs else {}

        if not windows:
            self.content.pack_start(
                label("Not running", "td-dim"), False, False, 0)
            return

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        rows = 0
        for window in windows:
            tabs = tabs_by_window.get(window.get_xid(), [])
            if tabs:
                # A browser window: show the window, then stack its tabs under it.
                box.pack_start(self._window_row(window, len(tabs)), False, False, 0)
                for tab in tabs[:MAX_ROWS - rows]:
                    box.pack_start(self._tab_row(window, tab), False, False, 0)
                    rows += 1
            else:
                box.pack_start(self._window_row(window), False, False, 0)
            rows += 1
            if rows >= MAX_ROWS:
                remaining = len(windows) - windows.index(window) - 1
                if remaining > 0:
                    box.pack_start(label(f"+{remaining} more", "td-dim"),
                                   False, False, 0)
                break

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(430)
        scroller.set_propagate_natural_height(True)
        scroller.add(box)
        self.content.pack_start(scroller, False, False, 0)

    def _row_button(self, indent=0):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("td-row")
        btn.set_margin_start(indent)
        return btn

    def _window_row(self, window, tab_count=0):
        btn = self._row_button()
        row = Gtk.Box(spacing=8)

        img = Gtk.Image()
        pixbuf = window.get_mini_icon() or window.get_icon()
        if pixbuf is not None:
            img.set_from_pixbuf(pixbuf.scale_simple(
                ROW_ICON, ROW_ICON, GdkPixbuf.InterpType.BILINEAR))
        row.pack_start(img, False, False, 0)

        title = window.get_name() or self.icon.name
        text = label(title)
        if window.is_minimized():
            text.get_style_context().add_class("td-dim")
        if window.is_active():
            text.get_style_context().add_class("td-title")
        row.pack_start(text, True, True, 0)

        if tab_count:
            row.pack_end(label(f"{tab_count} tabs", "td-dim", xalign=1.0),
                         False, False, 0)
        row.pack_end(self._close_button(window), False, False, 0)

        btn.add(row)
        btn.connect("clicked", self._on_window_clicked, window)
        return btn

    def _tab_row(self, window, tab):
        btn = self._row_button(indent=18)
        row = Gtk.Box(spacing=8)

        img = Gtk.Image()
        pixbuf = self.dock.tabs.favicon(tab)
        if pixbuf is not None:
            img.set_from_pixbuf(pixbuf)
        else:
            img.set_from_icon_name("text-html-symbolic", Gtk.IconSize.MENU)
        row.pack_start(img, False, False, 0)

        text = label(tab.title or tab.url)
        if tab.active:
            text.get_style_context().add_class("td-title")
        row.pack_start(text, True, True, 0)

        close = Gtk.Button()
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.add(Gtk.Image.new_from_icon_name("window-close-symbolic",
                                               Gtk.IconSize.MENU))
        close.connect("clicked", lambda _b: (self.dock.tabs.close(tab),
                                             self._refresh_soon()))
        row.pack_end(close, False, False, 0)

        btn.add(row)
        btn.connect("clicked", self._on_tab_clicked, window, tab)
        return btn

    def _close_button(self, window):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.add(Gtk.Image.new_from_icon_name("window-close-symbolic",
                                             Gtk.IconSize.MENU))
        btn.set_tooltip_text("Close window")

        def do_close(_b):
            window.close(Gtk.get_current_event_time())
            self._refresh_soon()

        btn.connect("clicked", do_close)
        return btn

    # -- actions -----------------------------------------------------------
    def _on_window_clicked(self, _btn, window):
        self.model.activate(window)
        self.dismiss()

    def _on_tab_clicked(self, _btn, window, tab):
        self.model.activate(window)
        self.dock.tabs.activate(tab)
        self.dismiss()

    def _refresh_soon(self):
        GLib.timeout_add(220, self._refresh)

    def _refresh(self):
        if not self.get_realized():
            return GLib.SOURCE_REMOVE
        if not self.model.windows_for(self.icon.key):
            self.dismiss()
            return GLib.SOURCE_REMOVE
        for child in self.content.get_children()[1:]:
            self.content.remove(child)
        self._build_rows()
        self.content.show_all()
        return GLib.SOURCE_REMOVE

    # -- hover lifetime ----------------------------------------------------
    def _on_enter(self, *_a):
        self.dock.cancel_window_list_close()
        return False

    def _on_leave(self, _w, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False       # moved onto a child widget, still inside
        self.dock.schedule_window_list_close()
        return False
