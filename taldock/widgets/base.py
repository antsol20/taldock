"""Base class for the small cairo-drawn items in the status area."""
from __future__ import annotations

from gi.repository import GLib, Gtk

from ..util import ICONS, now, paint_surface, rgba, rounded_rect


class PanelItem:
    """A status-area cell.

    Items are not GTK widgets; the status zone draws them all into one
    surface. That keeps the widget tree tiny and lets neighbouring items
    share hover/press styling for free.
    """

    #: Fixed width in logical px, or None to size from `measure()`.
    width = None
    #: Poll period in ms; 0 disables the timer.
    interval_ms = 0
    #: Set when the item wants pointer events.
    interactive = True

    def __init__(self, dock):
        self.dock = dock
        self.cfg = dock.cfg
        self.theme = dock.theme
        self.x = 0.0
        self.w = 0.0
        self.hover = False
        self.pressed = False
        self.popup = None
        self._timer = 0
        self._last_change = 0.0
        self.setup()
        if self.interval_ms:
            self.poll()
            self._timer = GLib.timeout_add(self.interval_ms, self._tick)

    # -- lifecycle ---------------------------------------------------------
    def setup(self):
        """Subclass hook: build state before the first draw."""

    def poll(self):
        """Subclass hook: refresh sampled data. Called on `interval_ms`."""

    def _tick(self):
        self.poll()
        return GLib.SOURCE_CONTINUE

    def destroy(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None

    # -- layout ------------------------------------------------------------
    def measure(self, height):
        """Return the item's desired width for a zone of `height` px."""
        return self.width or height

    def redraw(self):
        self.dock.queue_draw_status()

    # -- painting ----------------------------------------------------------
    def draw(self, cr, w, h):
        """Subclass hook. Origin is the item's top-left; clip is applied."""

    def draw_plate(self, cr, w, h, inset=2.0, radius=9.0):
        """Shared hover/press background so every item highlights alike."""
        if not (self.hover or self.pressed or self.popup):
            return
        col = self.theme["active"] if (self.pressed or self.popup) else self.theme["hover"]
        rgba(cr, col)
        rounded_rect(cr, inset, inset, w - inset * 2, h - inset * 2, radius)
        cr.fill()

    def draw_icon(self, cr, name, w, h, size=16, alpha=1.0, dx=0.0):
        surf = ICONS.surface(name, size, self.dock.scale, fallback=None)
        paint_surface(cr, surf, (w - size) / 2 + dx, (h - size) / 2, size,
                      self.dock.scale, alpha)

    def layout_text(self, cr, text, size=9.0, bold=False, color=None):
        """Create a pango layout using the panel font."""
        layout = self.dock.pango_layout(text, size, bold)
        rgba(cr, color or self.theme["fg"])
        return layout

    # -- input -------------------------------------------------------------
    def on_click(self, button, x, y, event):
        """Return True if the click was handled."""
        return False

    def on_scroll(self, direction, event):
        return False

    def tooltip(self):
        """Plain-text tooltip, or None."""
        return None

    # -- helpers -----------------------------------------------------------
    def mark_changed(self):
        self._last_change = now()

    def open_popup(self, popup):
        """Attach a popup and repaint when it closes."""
        self.popup = popup
        popup.connect("destroy", self._on_popup_closed)
        self.redraw()

    def _on_popup_closed(self, *_a):
        self.popup = None
        self.redraw()

    def close_popup(self):
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None
