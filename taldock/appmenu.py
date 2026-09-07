"""Applications menu: search, categories, session actions.

The menu is built once and reused. Rows live in a GtkListBox with filter and
sort functions, so searching and switching category only re-filter an
already-realised list. Rebuilding the rows costs ~300ms; re-filtering them
costs well under a millisecond, and the menu is on the Super key.
"""
from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

from .popup import Popup, label, separator
from .util import launch_first

# XDG main categories we surface, in display order.
CATEGORIES = [
    ("All", None),
    ("Favourites", "__fav__"),
    ("Accessories", "Utility"),
    ("Development", "Development"),
    ("Graphics", "Graphics"),
    ("Internet", "Network"),
    ("Multimedia", "AudioVideo"),
    ("Office", "Office"),
    ("Games", "Game"),
    ("System", "System"),
    ("Settings", "Settings"),
]

SESSION_ACTIONS = [
    ("Lock", "system-lock-screen-symbolic", ["xflock4"]),
    ("Log Out", "system-log-out-symbolic", ["xfce4-session-logout", "--logout"]),
    ("Suspend", "media-playback-pause-symbolic",
     ["xfce4-session-logout", "--suspend"]),
    ("Restart", "system-reboot-symbolic", ["xfce4-session-logout", "--reboot"]),
    ("Shut Down", "system-shutdown-symbolic", ["xfce4-session-logout", "--halt"]),
]


def score(query, app):
    """Rank an app against a lowercase query. Higher is better, 0 = no match."""
    name = app.get_name().lower()
    if name.startswith(query):
        return 100 - len(name) * 0.01
    if any(word.startswith(query) for word in name.split()):
        return 80 - len(name) * 0.01
    if query in name:
        return 60 - len(name) * 0.01

    for text in ((app.get_generic_name() or "").lower(),
                 (app.get_description() or "").lower(),
                 " ".join(app.get_keywords() or []).lower(),
                 (app.get_executable() or "").lower()):
        if text.startswith(query):
            return 50
        if query in text:
            return 34

    # Last resort: in-order subsequence, which catches acronyms.
    position = 0
    for char in query:
        position = name.find(char, position) + 1
        if position == 0:
            return 0
    return 18


class AppRow(Gtk.ListBoxRow):
    """One application. Built once; shown or hidden by the filter."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.match = 1.0        # current search score, 0 = filtered out
        self.sort_name = app.get_name().lower()
        self.categories = set((app.get_categories() or "").split(";")) - {""}
        self.get_style_context().add_class("td-row")

        row = Gtk.Box(spacing=10)
        row.set_margin_top(3)
        row.set_margin_bottom(3)
        image = Gtk.Image()
        icon = app.get_icon()
        if icon is not None:
            image.set_from_gicon(icon, Gtk.IconSize.LARGE_TOOLBAR)
        else:
            image.set_from_icon_name("application-x-executable",
                                     Gtk.IconSize.LARGE_TOOLBAR)
        row.pack_start(image, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.pack_start(label(app.get_name()), False, False, 0)
        subtitle = app.get_description() or app.get_generic_name() or ""
        if subtitle:
            text.pack_start(label(subtitle, "td-dim"), False, False, 0)
        row.pack_start(text, True, True, 0)
        self.add(row)


class AppMenuPopup(Popup):
    def __init__(self, dock):
        # Persistent: the dock keeps one of these and shows/hides it.
        super().__init__(dock, padding=0, persistent=True)
        self.dock = dock
        self.appdb = dock.appdb
        self.category = None
        self.query = ""

        self.content.get_style_context().add_class("td-popup")
        self.content.set_size_request(452, 0)
        for setter, getter in (
                (self.content.set_margin_start, self.content.get_margin_start),
                (self.content.set_margin_end, self.content.get_margin_end),
                (self.content.set_margin_top, self.content.get_margin_top),
                (self.content.set_margin_bottom, self.content.get_margin_bottom)):
            setter(getter() + 12)

        self._build_header()
        self._build_body()
        self.connect("key-press-event", self._on_key)
        self.populate()

    # -- chrome ------------------------------------------------------------
    def _build_header(self):
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search applications…")
        self.search.get_style_context().add_class("td-search")
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("activate", lambda _e: self._launch_selected())
        self.content.pack_start(self.search, False, False, 0)

    def _build_body(self):
        body = Gtk.Box(spacing=10)
        body.set_margin_top(4)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        sidebar.set_size_request(126, -1)
        self.category_buttons = []
        group = None
        for title, key in CATEGORIES:
            btn = Gtk.RadioButton.new_with_label_from_widget(group, title)
            if group is None:
                group = btn
            btn.set_mode(False)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("td-row")
            btn.set_property("xalign", 0.0)
            # Never take focus: it must stay in the search entry, or typing
            # after picking a category would go nowhere.
            btn.set_can_focus(False)
            btn.connect("toggled", self._on_category, key)
            sidebar.pack_start(btn, False, False, 0)
            self.category_buttons.append(btn)
        body.pack_start(sidebar, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.set_filter_func(self._filter)
        self.listbox.set_sort_func(self._sort)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("button-press-event", self._on_list_button)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_size_request(-1, 372)
        self.scroller.add(self.listbox)
        body.pack_start(self.scroller, True, True, 0)

        self.content.pack_start(body, True, True, 0)
        self.content.pack_start(separator(), False, False, 2)
        self.content.pack_start(self._build_footer(), False, False, 0)

    def _build_footer(self):
        footer = Gtk.Box(spacing=6)
        footer.pack_start(
            label(GLib.get_real_name() or GLib.get_user_name(), "td-dim"),
            True, True, 4)
        for title, icon_name, command in SESSION_ACTIONS:
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("td-row")
            btn.set_tooltip_text(title)
            btn.set_can_focus(False)       # keep focus in the search entry
            btn.add(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON))
            btn.connect("clicked", self._on_session, command)
            footer.pack_end(btn, False, False, 0)
        return footer

    # -- rows --------------------------------------------------------------
    def populate(self):
        """Build one row per installed application. Called rarely."""
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.rows = []
        for app in self.appdb.by_id.values():
            if not app.should_show():
                continue
            row = AppRow(app)
            self.listbox.add(row)
            self.rows.append(row)
        self.listbox.show_all()
        self._reapply()

    # -- filter / sort -----------------------------------------------------
    def _filter(self, row):
        return row.match > 0

    def _sort(self, a, b):
        if a.match != b.match:
            return -1 if a.match > b.match else 1
        if a.sort_name == b.sort_name:
            return 0
        return -1 if a.sort_name < b.sort_name else 1

    def _reapply(self):
        """Recompute each row's match against the current query/category."""
        query = self.query
        favourites = None
        if not query and self.category == "__fav__":
            favourites = set(self.dock.cfg["launchers"])

        for row in self.rows:
            if query:
                # A search looks at every app; the category is ignored while
                # typing, and cleared as soon as a category is clicked.
                row.match = score(query, row.app)
            elif favourites is not None:
                row.match = 1.0 if row.app.get_id() in favourites else 0.0
            elif self.category:
                row.match = 1.0 if self.category in row.categories else 0.0
            else:
                row.match = 1.0
        self.listbox.invalidate_filter()
        self.listbox.invalidate_sort()
        self._select_first()

    def _visible_rows(self):
        return [r for r in self.rows if r.match > 0]

    def _select_first(self):
        rows = sorted(self._visible_rows(),
                      key=lambda r: (-r.match, r.sort_name))
        if rows:
            self.listbox.select_row(rows[0])
            self.scroller.get_vadjustment().set_value(0)
        else:
            self.listbox.select_row(None)

    # -- input -------------------------------------------------------------
    def _on_search_changed(self, entry):
        self.query = entry.get_text().strip().lower()
        self._reapply()

    def _on_category(self, button, key):
        if not button.get_active():
            return
        self.category = key
        # Without this, picking a category while a search is active appears to
        # do nothing: the query wins in _reapply and the list never changes.
        if self.query:
            self.search.set_text("")     # triggers _on_search_changed
        else:
            self._reapply()
        self.search.grab_focus()

    def _on_row_activated(self, _listbox, row):
        self._launch(row.app)

    def _on_list_button(self, _listbox, event):
        if event.button != 3:
            return False
        row = _listbox.get_row_at_y(int(event.y))
        if row is None:
            return False
        self._context_menu(row.app, event)
        return True

    def _move_selection(self, delta):
        rows = sorted(self._visible_rows(), key=lambda r: (-r.match, r.sort_name))
        if not rows:
            return
        current = self.listbox.get_selected_row()
        index = rows.index(current) if current in rows else 0
        index = max(0, min(len(rows) - 1, index + delta))
        row = rows[index]
        self.listbox.select_row(row)
        self._scroll_into_view(row)

    def _scroll_into_view(self, row):
        adjustment = self.scroller.get_vadjustment()
        allocation = row.get_allocation()
        page = adjustment.get_page_size()
        value = adjustment.get_value()
        if allocation.y < value:
            adjustment.set_value(allocation.y)
        elif allocation.y + allocation.height > value + page:
            adjustment.set_value(allocation.y + allocation.height - page)

    def _on_key(self, _widget, event):
        key = event.keyval
        if key in (Gdk.KEY_Escape, Gdk.KEY_Super_L, Gdk.KEY_Super_R,
                   Gdk.KEY_Meta_L, Gdk.KEY_Meta_R):
            # Super has to be handled here rather than by the desktop
            # shortcut: while the menu is open we hold a seat grab, so
            # xfwm4 never sees the key and could not toggle us shut.
            self.dismiss()
            return True
        if key in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move_selection(1 if key == Gdk.KEY_Down else -1)
            return True
        if key in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self._move_selection(8 if key == Gdk.KEY_Page_Down else -8)
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._launch_selected()
            return True
        # Everything else -- Backspace, Delete, arrows within the text,
        # printable characters -- belongs to the search entry, which keeps
        # focus for exactly this reason.
        return False

    # -- actions -----------------------------------------------------------
    def _launch(self, app):
        self.dismiss()
        try:
            app.launch([], Gdk.Display.get_default().get_app_launch_context())
        except GLib.Error as exc:
            print(f"taldock: could not launch {app.get_id()}: {exc}")

    def _launch_selected(self):
        row = self.listbox.get_selected_row()
        if row is not None and row.match > 0:
            self._launch(row.app)

    def _context_menu(self, app, event):
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Add to Dock")
        item.connect("activate", lambda _i: self._pin(app))
        menu.append(item)
        for action in app.list_actions():
            entry = Gtk.MenuItem(label=app.get_action_name(action))
            entry.connect("activate", lambda _i, a=action: (
                self.dismiss(),
                app.launch_action(
                    a, Gdk.Display.get_default().get_app_launch_context())))
            menu.append(entry)
        menu.show_all()
        menu.attach_to_widget(self, None)
        self.dock.keep_menu(menu)
        menu.popup_at_pointer(event)

    def _pin(self, app):
        launchers = list(self.dock.cfg["launchers"])
        if app.get_id() not in launchers:
            launchers.append(app.get_id())
            self.dock.cfg["launchers"] = launchers
            self.dock.cfg.save()
            self.dock.launchers.rebuild()
        self.dismiss()

    def _on_session(self, _btn, command):
        self.dismiss()
        if not launch_first([command]):
            print(f"taldock: session action unavailable: {' '.join(command)}")

    # -- reuse -------------------------------------------------------------
    def reset(self):
        """Return to a clean state before showing again."""
        self.category = None
        if self.category_buttons:
            self.category_buttons[0].set_active(True)
        if self.search.get_text():
            self.search.set_text("")
        else:
            self.query = ""
            self._reapply()

    def open_at(self, anchor_x, align="start"):
        super().open_at(anchor_x, align)
        self.search.grab_focus()
