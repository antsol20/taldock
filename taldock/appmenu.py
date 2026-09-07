"""Applications menu: search, categories, session actions."""
from __future__ import annotations

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from .popup import Popup, css_provider, label, separator

# XDG main categories we surface, in display order.
CATEGORIES = [
    ("All", "view-grid-symbolic", None),
    ("Favourites", "starred-symbolic", "__fav__"),
    ("Accessories", "applications-utilities", "Utility"),
    ("Development", "applications-development", "Development"),
    ("Graphics", "applications-graphics", "Graphics"),
    ("Internet", "applications-internet", "Network"),
    ("Multimedia", "applications-multimedia", "AudioVideo"),
    ("Office", "applications-office", "Office"),
    ("Games", "applications-games", "Game"),
    ("System", "applications-system", "System"),
    ("Settings", "preferences-desktop", "Settings"),
]

SESSION_ACTIONS = [
    ("Lock", "system-lock-screen-symbolic",
     ["xflock4"]),
    ("Log Out", "system-log-out-symbolic",
     ["xfce4-session-logout", "--logout"]),
    ("Suspend", "media-playback-pause-symbolic",
     ["xfce4-session-logout", "--suspend"]),
    ("Restart", "system-reboot-symbolic",
     ["xfce4-session-logout", "--reboot"]),
    ("Shut Down", "system-shutdown-symbolic",
     ["xfce4-session-logout", "--halt"]),
]


def score(query, app):
    """Rank an app against a lowercase query. Higher is better, 0 = no match."""
    name = app.get_name().lower()
    if name.startswith(query):
        return 100 - len(name) * 0.01
    words = name.split()
    if any(word.startswith(query) for word in words):
        return 80 - len(name) * 0.01
    if query in name:
        return 60 - len(name) * 0.01

    haystacks = [
        (app.get_generic_name() or "").lower(),
        (app.get_description() or "").lower(),
        " ".join(app.get_keywords() or []).lower(),
        (app.get_executable() or "").lower(),
    ]
    for text in haystacks:
        if text.startswith(query):
            return 50
        if query in text:
            return 34

    # Last resort: in-order subsequence, which catches acronyms like "gimp".
    position = 0
    for char in query:
        position = name.find(char, position) + 1
        if position == 0:
            return 0
    return 18


class AppMenuPopup(Popup):
    def __init__(self, dock):
        super().__init__(dock, padding=0)
        self.dock = dock
        self.appdb = dock.appdb
        self.category = None
        self.rows = []
        self.selected = 0

        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), css_provider(self.theme),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.content.get_style_context().add_class("td-popup")
        self.content.set_size_request(452, 0)
        self.content.set_margin_start(self.content.get_margin_start() + 12)
        self.content.set_margin_end(self.content.get_margin_end() + 12)
        self.content.set_margin_top(self.content.get_margin_top() + 12)
        self.content.set_margin_bottom(self.content.get_margin_bottom() + 12)

        self._build_header()
        self._build_body()
        self.connect("key-press-event", self._on_key)
        self.refresh()

    # -- chrome ------------------------------------------------------------
    def _build_header(self):
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search applications…")
        self.search.get_style_context().add_class("td-search")
        self.search.connect("search-changed", lambda _e: self.refresh())
        self.search.connect("activate", lambda _e: self._launch_selected())
        self.content.pack_start(self.search, False, False, 0)

    def _build_body(self):
        body = Gtk.Box(spacing=10)
        body.set_margin_top(4)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        sidebar.set_size_request(126, -1)
        self.category_buttons = []
        group = None
        for title, icon_name, key in CATEGORIES:
            btn = Gtk.RadioButton.new_with_label_from_widget(group, title)
            if group is None:
                group = btn
            btn.set_mode(False)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("td-row")
            btn.set_property("xalign", 0.0)
            btn.connect("toggled", self._on_category, key)
            sidebar.pack_start(btn, False, False, 0)
            self.category_buttons.append(btn)
        body.pack_start(sidebar, False, False, 0)

        self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(-1, 372)
        scroller.add(self.listbox)
        self.scroller = scroller
        body.pack_start(scroller, True, True, 0)

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
            btn.add(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON))
            btn.connect("clicked", self._on_session, command)
            footer.pack_end(btn, False, False, 0)
        return footer

    # -- content -----------------------------------------------------------
    def _on_category(self, button, key):
        if button.get_active():
            self.category = key
            self.refresh()

    def _apps(self):
        query = self.search.get_text().strip().lower()
        apps = [a for a in self.appdb.by_id.values() if a.should_show()]

        if query:
            ranked = [(score(query, a), a) for a in apps]
            ranked = [(s, a) for s, a in ranked if s > 0]
            ranked.sort(key=lambda pair: (-pair[0], pair[1].get_name().lower()))
            return [a for _s, a in ranked[:60]]

        if self.category == "__fav__":
            pinned = self.dock.cfg["launchers"]
            found = [self.appdb.get(p) for p in pinned]
            return [a for a in found if a is not None]
        if self.category:
            apps = [a for a in apps
                    if self.category in (a.get_categories() or "").split(";")]
        apps.sort(key=lambda a: a.get_name().lower())
        return apps

    def refresh(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.rows = []
        for app in self._apps():
            row = self._app_row(app)
            self.listbox.pack_start(row, False, False, 0)
            self.rows.append((row, app))
        if not self.rows:
            self.listbox.pack_start(
                label("No matching applications", "td-dim"), False, False, 8)
        self.selected = 0
        self._sync_selection()
        self.listbox.show_all()

    def _app_row(self, app):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("td-row")
        row = Gtk.Box(spacing=10)

        img = Gtk.Image()
        icon = app.get_icon()
        if icon is not None:
            img.set_from_gicon(icon, Gtk.IconSize.LARGE_TOOLBAR)
        else:
            img.set_from_icon_name("application-x-executable",
                                   Gtk.IconSize.LARGE_TOOLBAR)
        row.pack_start(img, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.pack_start(label(app.get_name()), False, False, 0)
        subtitle = app.get_description() or app.get_generic_name() or ""
        if subtitle:
            text.pack_start(label(subtitle, "td-dim"), False, False, 0)
        row.pack_start(text, True, True, 0)

        btn.add(row)
        btn.connect("clicked", lambda _b, a=app: self._launch(a))
        btn.connect("button-press-event", self._on_row_button, app)
        return btn

    def _sync_selection(self):
        # A style class, not StateFlags.SELECTED: buttons do not carry a
        # selected state, so the flag would never paint.
        for index, (row, _app) in enumerate(self.rows):
            ctx = row.get_style_context()
            if index == self.selected:
                ctx.add_class("td-selected")
            else:
                ctx.remove_class("td-selected")
        if 0 <= self.selected < len(self.rows):
            row = self.rows[self.selected][0]
            row.grab_focus()
            self._scroll_into_view(row)

    def _scroll_into_view(self, row):
        """Keep the keyboard selection visible as it moves down the list."""
        adjustment = self.scroller.get_vadjustment()
        allocation = row.get_allocation()
        top, bottom = allocation.y, allocation.y + allocation.height
        page = adjustment.get_page_size()
        value = adjustment.get_value()
        if top < value:
            adjustment.set_value(top)
        elif bottom > value + page:
            adjustment.set_value(bottom - page)

    # -- actions -----------------------------------------------------------
    def _launch(self, app):
        self.dismiss()
        try:
            app.launch([], Gdk.Display.get_default().get_app_launch_context())
        except GLib.Error as exc:
            print(f"taldock: could not launch {app.get_id()}: {exc}")

    def _launch_selected(self):
        if 0 <= self.selected < len(self.rows):
            self._launch(self.rows[self.selected][1])

    def _on_row_button(self, _widget, event, app):
        if event.button != 3:
            return False
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
        return True

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
        try:
            GLib.spawn_async(["/usr/bin/env"] + command,
                             flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error as exc:
            print(f"taldock: session action failed: {exc}")

    # -- keyboard ----------------------------------------------------------
    def _on_key(self, _widget, event):
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self.dismiss()
            return True
        if key in (Gdk.KEY_Down, Gdk.KEY_Up):
            if self.rows:
                step = 1 if key == Gdk.KEY_Down else -1
                self.selected = max(0, min(len(self.rows) - 1,
                                           self.selected + step))
                self._sync_selection()
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._launch_selected()
            return True
        # Anything else types into the search box.
        if not self.search.has_focus() and event.string and event.string.isprintable():
            self.search.grab_focus()
            self.search.set_text(self.search.get_text() + event.string)
            self.search.set_position(-1)
            return True
        return False

    def open_at(self, anchor_x, align="start"):
        super().open_at(anchor_x, align)
        self.search.grab_focus()
