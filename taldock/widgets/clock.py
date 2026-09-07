"""Clock with a hand-drawn calendar popup."""
from __future__ import annotations

import calendar
import datetime as dt

from gi.repository import GLib, Gtk

from ..popup import Popup, css_provider, label, separator
from ..util import rgba, rounded_rect, with_alpha
from .base import PanelItem

CELL = 30.0
HEAD = 26.0


class ClockItem(PanelItem):
    def setup(self):
        self.fmt = self.cfg.get("clock_format", "%H:%M")
        self.date_fmt = self.cfg.get("clock_date_format", "%a %d %b")
        self.show_date = bool(self.cfg.get("show_clock_date", True))
        self.text = ""
        self.date_text = ""
        self._timer = 0
        self._refresh()
        self._schedule()

    def _schedule(self):
        """Wake exactly on the next minute boundary rather than polling."""
        now = dt.datetime.now()
        delay = 60 - now.second + (1 if now.microsecond else 0)
        self._timer = GLib.timeout_add_seconds(max(1, delay), self._on_tick)

    def _on_tick(self):
        self._refresh()
        self._schedule()
        return GLib.SOURCE_REMOVE

    def _refresh(self):
        now = dt.datetime.now()
        text, date_text = now.strftime(self.fmt), now.strftime(self.date_fmt)
        if (text, date_text) != (self.text, self.date_text):
            self.text, self.date_text = text, date_text
            self.dock.invalidate_status_layout()

    def measure(self, height):
        w1 = self.dock.text_width(self.text, 10.5, bold=True)
        w2 = self.dock.text_width(self.date_text, 8.0) if self.show_date else 0
        return max(w1, w2) + 20

    def draw(self, cr, w, h):
        self.draw_plate(cr, w, h)
        if self.show_date:
            time_l = self.dock.pango_layout(self.text, 10.5, bold=True)
            date_l = self.dock.pango_layout(self.date_text, 8.0)
            th = time_l.get_pixel_size().height
            dh = date_l.get_pixel_size().height
            top = (h - (th + dh - 2)) / 2
            rgba(cr, self.theme["fg"])
            cr.move_to((w - time_l.get_pixel_size().width) / 2, top)
            self.dock.show_layout(cr, time_l)
            rgba(cr, self.theme["fg_dim"])
            cr.move_to((w - date_l.get_pixel_size().width) / 2, top + th - 2)
            self.dock.show_layout(cr, date_l)
        else:
            lay = self.dock.pango_layout(self.text, 11.0, bold=True)
            sz = lay.get_pixel_size()
            rgba(cr, self.theme["fg"])
            cr.move_to((w - sz.width) / 2, (h - sz.height) / 2)
            self.dock.show_layout(cr, lay)

    def tooltip(self):
        return dt.datetime.now().strftime("%A, %d %B %Y")

    # -- popup -------------------------------------------------------------
    def on_click(self, button, x, y, event):
        if button != 1:
            return False
        if self.popup:
            self.close_popup()
            return True
        popup = CalendarPopup(self.dock, self.theme)
        self.open_popup(popup)
        popup.open_at(self.dock.item_center_root(self))
        return True


class CalendarPopup(Popup):
    """Month grid drawn with cairo so it matches the dock exactly."""

    def __init__(self, dock, theme):
        super().__init__(dock, padding=12)
        self.theme = theme
        self.today = dt.date.today()
        self.shown = self.today.replace(day=1)

        self.content.set_size_request(int(CELL * 7), -1)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), css_provider(theme),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.content.get_style_context().add_class("td-popup")

        header = Gtk.Box(spacing=4)
        self.title = label("", "td-title", xalign=0.5)
        prev_btn = self._nav("‹", -1)
        next_btn = self._nav("›", +1)
        header.pack_start(prev_btn, False, False, 0)
        header.pack_start(self.title, True, True, 0)
        header.pack_start(next_btn, False, False, 0)
        self.content.pack_start(header, False, False, 0)

        self.grid = Gtk.DrawingArea()
        self.grid.set_size_request(int(CELL * 7), int(HEAD + CELL * 6))
        self.grid.connect("draw", self._draw_grid)
        self.content.pack_start(self.grid, False, False, 0)

        self.content.pack_start(separator(), False, False, 3)
        self.footer = label(self.today.strftime("%A, %d %B %Y"), "td-dim",
                            xalign=0.5)
        self.content.pack_start(self.footer, False, False, 0)
        self._sync()

    def _nav(self, glyph, delta):
        btn = Gtk.Button(label=glyph)
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("td-btn")
        btn.connect("clicked", lambda _b: self._shift(delta))
        return btn

    def _shift(self, delta):
        month = self.shown.month - 1 + delta
        year = self.shown.year + month // 12
        self.shown = dt.date(year, month % 12 + 1, 1)
        self._sync()

    def _sync(self):
        self.title.set_text(self.shown.strftime("%B %Y"))
        self.grid.queue_draw()

    def _draw_grid(self, widget, cr):
        w = widget.get_allocated_width()
        cell = w / 7.0
        # Monday-first weekday initials.
        rgba(cr, self.theme["fg_faint"])
        for i, name in enumerate(calendar.day_abbr):
            lay = self.dock.pango_layout(name[:2], 8.0, bold=True)
            sz = lay.get_pixel_size()
            cr.move_to(i * cell + (cell - sz.width) / 2, (HEAD - sz.height) / 2)
            self.dock.show_layout(cr, lay)

        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(
            self.shown.year, self.shown.month)
        for row, week in enumerate(weeks):
            for col, day in enumerate(week):
                cx = col * cell + cell / 2
                cy = HEAD + row * CELL + CELL / 2
                in_month = day.month == self.shown.month
                if day == self.today:
                    rgba(cr, self.theme["accent"])
                    rounded_rect(cr, cx - 13, cy - 13, 26, 26, 13)
                    cr.fill()
                    colour = (0.08, 0.08, 0.12, 1.0)
                elif not in_month:
                    colour = with_alpha(self.theme["fg_faint"], 0.45)
                elif col >= 5:
                    colour = self.theme["fg_dim"]
                else:
                    colour = self.theme["fg"]
                lay = self.dock.pango_layout(str(day.day), 9.0,
                                             bold=day == self.today)
                sz = lay.get_pixel_size()
                rgba(cr, colour)
                cr.move_to(cx - sz.width / 2, cy - sz.height / 2)
                self.dock.show_layout(cr, lay)
        return False
