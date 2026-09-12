"""Applications menu: search, categories, session actions.

The menu is built once and reused. Rows live in a GtkListBox with filter and
sort functions, so searching and switching category only re-filter an
already-realised list. Rebuilding the rows costs ~300ms; re-filtering them
costs well under a millisecond, and the menu is on the Super key.
"""
from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

from .popup import GAP, SHADOW, Popup, label, separator
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
        # Which column the arrow keys drive. Focus always stays in the search
        # entry (see _on_key), so this is our own notion, not GTK focus.
        self.pane = "apps"
        self._syncing_category = False
        # Set when applications were installed or removed while we were on
        # screen; the rows are rebuilt on the way out instead.
        self._stale = False

        self.content.get_style_context().add_class("td-popup")
        cfg = dock.cfg
        self.sidebar_width = max(0, int(cfg["menu_sidebar_width"]))
        self.content.set_size_request(self._clamped_width(), 0)
        for setter, getter in (
                (self.content.set_margin_start, self.content.get_margin_start),
                (self.content.set_margin_end, self.content.get_margin_end),
                (self.content.set_margin_top, self.content.get_margin_top),
                (self.content.set_margin_bottom, self.content.get_margin_bottom)):
            setter(getter() + 12)

        self._build_header()
        self._build_body()
        self.connect("dismissed", lambda *_a: self._schedule_rebuild())
        # No connect() for key-press-event here: Popup.__init__ already
        # connected self._on_key, which resolves to the override below.
        # Connecting it again ran the handler twice for every keypress.
        self.populate()

    # -- sizing ------------------------------------------------------------
    def _clamped_width(self):
        """Configured width, kept inside the monitor."""
        mon = self.dock.monitor_geometry()
        side = int(self.dock.cfg["side_margin"])
        return max(300, min(int(self.dock.cfg["menu_width"]),
                            mon.width - side * 2))

    def _fit_to_screen(self):
        """Shrink the list if the configured height would run off-screen.

        Must run with the widgets visible: an unshown window reports only a
        minimum size, so the chrome would measure as nonsense. The chrome
        (search box, footer, padding) is measured rather than guessed, so
        this stays right if the layout changes.
        """
        wanted = int(self.dock.cfg["menu_height"])
        self.scroller.set_size_request(-1, wanted)
        total = self.get_preferred_size()[1].height
        chrome = total - wanted
        mon = self.dock.monitor_geometry()
        available = (mon.height - self.dock.bar_height
                     - int(self.dock.cfg["margin"]) - GAP - SHADOW * 2 - chrome)
        height = max(140, min(wanted, int(available)))
        if height != wanted:
            self.scroller.set_size_request(-1, height)
        return height

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
        sidebar.set_size_request(self.sidebar_width, -1)
        sidebar.set_valign(Gtk.Align.START)
        self.category_buttons = []
        group = None
        for title, key in CATEGORIES:
            btn = Gtk.RadioButton.new_with_label_from_widget(group, title)
            if group is None:
                group = btn
            btn.set_mode(False)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("td-row")
            btn.get_style_context().add_class("td-cat")
            btn.set_property("xalign", 0.0)
            # Never take focus: it must stay in the search entry, or typing
            # after picking a category would go nowhere.
            btn.set_can_focus(False)
            btn.connect("toggled", self._on_category, key)
            sidebar.pack_start(btn, False, False, 0)
            self.category_buttons.append(btn)
        # The category column is taller than a small list would be, so it
        # would otherwise set a floor that menu_height could not go below.
        # Letting it scroll makes the configured height actually apply.
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_size_request(self.sidebar_width, -1)
        sidebar_scroll.add(sidebar)
        if self.sidebar_width > 0:
            body.pack_start(sidebar_scroll, False, False, 0)
        self._sidebar = sidebar_scroll

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.set_filter_func(self._filter)
        self.listbox.set_sort_func(self._sort)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("button-press-event", self._on_list_button)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_size_request(-1, int(self.dock.cfg["menu_height"]))
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
        if self.query:
            # A search is over applications, so Down should move through the
            # results rather than through categories you are no longer in.
            self._set_pane("apps")
        self._reapply()

    def _on_category(self, button, key):
        if not button.get_active():
            return
        self.category = key
        if not self._syncing_category:
            # Clicked, not arrowed into: the next Down belongs to the apps.
            self._set_pane("apps")
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

    # -- panes -------------------------------------------------------------
    def _set_pane(self, pane):
        """Point the arrow keys at the categories or the applications.

        Focus is not moved: it has to stay in the search entry or Backspace
        and the text cursor stop working (see the note in _on_key). So the
        active pane is a style class instead -- the idle pane keeps a quieter
        version of its highlight rather than losing it, so you can see where
        you will land when you step back.
        """
        if pane == "categories" and (self.sidebar_width <= 0
                                     or not self.category_buttons):
            # The buttons still exist when menu_sidebar_width is 0, they are
            # just never packed; arrowing into an invisible column would look
            # like the keyboard had stopped working.
            return
        self.pane = pane
        cats = pane == "categories"
        ctx = self.listbox.get_style_context()
        (ctx.add_class if cats else ctx.remove_class)("td-idle")
        for btn in self.category_buttons:
            ctx = btn.get_style_context()
            (ctx.add_class if cats else ctx.remove_class)("td-active")

    def _move_category(self, delta):
        buttons = self.category_buttons
        if not buttons:
            return
        index = next((i for i, b in enumerate(buttons) if b.get_active()), 0)
        index = max(0, min(len(buttons) - 1, index + delta))
        button = buttons[index]
        if button.get_active():
            return
        # _on_category would otherwise read this as a mouse click and hand
        # the arrows straight back to the application list.
        self._syncing_category = True
        try:
            button.set_active(True)
        finally:
            self._syncing_category = False
        self._scroll_category_into_view(button)

    def _scroll_category_into_view(self, button):
        adjustment = self._sidebar.get_vadjustment()
        allocation = button.get_allocation()
        page = adjustment.get_page_size()
        value = adjustment.get_value()
        if allocation.y < value:
            adjustment.set_value(allocation.y)
        elif allocation.y + allocation.height > value + page:
            adjustment.set_value(allocation.y + allocation.height - page)

    def _caret_can_leave(self, going_left):
        """True if Left/Right is free to change pane rather than move the caret.

        With text in the box those keys still belong to the entry, or the
        query could not be edited; they only cross into the sidebar once the
        caret has run out of text in that direction.
        """
        text = self.search.get_text()
        if not text:
            return True
        if self.search.get_selection_bounds():
            return False
        position = self.search.get_position()
        return position == 0 if going_left else position >= len(text)

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
        if key in (Gdk.KEY_Left, Gdk.KEY_Right):
            if not self._caret_can_leave(key == Gdk.KEY_Left):
                return False        # still editing the query
            self._set_pane("categories" if key == Gdk.KEY_Left else "apps")
            return True
        step = 1 if key in (Gdk.KEY_Down, Gdk.KEY_Page_Down) else -1
        if key in (Gdk.KEY_Down, Gdk.KEY_Up):
            if self.pane == "categories":
                self._move_category(step)
            else:
                self._move_selection(step)
            return True
        if key in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            if self.pane == "categories":
                self._move_category(step * 4)
            else:
                self._move_selection(step * 8)
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.pane == "categories":
                # The category is already applied; Enter just says "now the
                # apps", which is what Right does too.
                self._set_pane("apps")
            else:
                self._launch_selected()
            return True
        if key == Gdk.KEY_Tab:
            self._set_pane("apps" if self.pane == "categories"
                           else "categories")
            return True
        if self._media_key(event):
            return True
        # Everything else -- Backspace, Delete, Home/End, printable
        # characters, and Left/Right while there is still text to move
        # through -- belongs to the search entry, which keeps focus for
        # exactly this reason.
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

    # -- keeping up with installed applications ----------------------------
    def refresh_apps(self):
        """An application was installed or removed: the rows are out of date."""
        self._stale = True
        self._schedule_rebuild()

    def _schedule_rebuild(self):
        # dismiss() emits "dismissed" before it hides, so even on the way out
        # we are still visible right now. An idle lands after the hide.
        if self._stale:
            GLib.idle_add(self._rebuild_if_hidden)

    def _rebuild_if_hidden(self):
        """Rebuild the rows, but never under the user's hands.

        populate() throws every row away, so doing it while the menu is open
        would drop a typed search and its selection mid-keystroke. It costs
        ~300ms too, which is exactly what prewarming exists to keep off the
        Super key -- so it waits for the menu to be closed.
        """
        if self.get_visible():
            return GLib.SOURCE_REMOVE      # reopened; catch it on the next close
        self.populate()
        self._stale = False
        return GLib.SOURCE_REMOVE

    # -- reuse -------------------------------------------------------------
    def prewarm(self):
        super().prewarm()
        self.show_all()
        self._fit_to_screen()
        self.hide()

    def reset(self):
        """Return to a clean state before showing again."""
        self.category = None
        self._set_pane("apps")
        if self.category_buttons:
            self.category_buttons[0].set_active(True)
        if self.search.get_text():
            self.search.set_text("")
        else:
            self.query = ""
            self._reapply()

    def open_at(self, anchor_x, align="start"):
        self.show_all()          # sizes only mean anything once visible
        self._fit_to_screen()
        super().open_at(anchor_x, align)
        self.search.grab_focus()
