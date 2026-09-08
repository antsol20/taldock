"""Floating rounded panel used by every dock popup."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from .util import draw_shadow, ease_out_cubic, now, rgba, rounded_rect

SHADOW = 14      # px of transparent margin reserved for the drop shadow
RADIUS = 14
GAP = 9          # distance between the dock bar and the popup


class Popup(Gtk.Window):
    """An override-redirect window that dismisses itself on outside input.

    Content goes in `self.content`, a GtkBox already inset past the shadow
    margin. Call `open_at()` to position, grab and animate it in.
    """

    __gsignals__ = {"dismissed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, dock, padding=10, grab=True, persistent=False):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.dock = dock
        self.theme = dock.theme
        # A seat grab is right for click-opened popups, but fatal for hover
        # previews: the grab makes the dock see a leave, which closes the
        # popup, which restores the hover -- an open/close flicker loop.
        self._want_grab = grab
        self._grabbed = False
        # A persistent popup is hidden rather than destroyed, so it can be
        # shown again without paying to rebuild and realise its contents.
        self.persistent = persistent
        self._anim = None
        self._t0 = 0.0

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content.set_margin_start(SHADOW + padding)
        self.content.set_margin_end(SHADOW + padding)
        self.content.set_margin_top(SHADOW + padding)
        self.content.set_margin_bottom(SHADOW + padding)
        self.add(self.content)

        self.connect("draw", self._on_draw)
        self.connect("key-press-event", self._on_key)
        self.connect("button-press-event", self._on_button)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.KEY_PRESS_MASK)

    # -- painting ----------------------------------------------------------
    def _on_draw(self, _w, cr):
        alloc = self.get_allocation()
        x, y = SHADOW, SHADOW
        w = alloc.width - SHADOW * 2
        h = alloc.height - SHADOW * 2
        cr.set_operator(1)  # SOURCE: clear the whole surface first
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)  # OVER

        draw_shadow(cr, x, y, w, h, RADIUS, SHADOW, self.theme["shadow"])
        rgba(cr, self.theme["popup_bg"])
        rounded_rect(cr, x, y, w, h, RADIUS)
        cr.fill_preserve()
        rgba(cr, self.theme["popup_border"])
        cr.set_line_width(1.0)
        cr.stroke()
        return False

    # -- placement ---------------------------------------------------------
    def open_at(self, anchor_x, align="center"):
        """Show the popup horizontally anchored to `anchor_x` (root coords)."""
        self.show_all()
        w, h = self.get_preferred_size()[1].width, self.get_preferred_size()[1].height
        mon = self.dock.monitor_geometry()
        bar = self.dock.bar_rect_root()

        if align == "center":
            x = int(anchor_x - w / 2)
        elif align == "start":
            x = int(anchor_x - SHADOW)
        else:
            x = int(anchor_x - w + SHADOW)
        # Keep the card on screen, allowing for the invisible shadow margin.
        x = max(mon.x + self.dock.cfg["side_margin"] - SHADOW,
                min(x, mon.x + mon.width - w - self.dock.cfg["side_margin"] + SHADOW))

        # The visible card sits SHADOW px inside the window on every side, so
        # solve for the card edge and convert back to a window position.
        if self.dock.at_bottom():
            y = bar.y - GAP - h + SHADOW
        else:
            y = bar.y + bar.height + GAP - SHADOW
        self.move(x, y)
        if self._want_grab:
            self._grab()
        self._animate_in()

    def _grab(self, attempt=0):
        """Take a seat grab so clicks anywhere else dismiss us.

        When opened from a keyboard shortcut the window manager may still
        hold the keyboard, so the first attempt can fail; retry briefly
        rather than leaving the popup unable to receive typing.
        """
        window = self.get_window()
        if window is None:
            return
        seat = Gdk.Display.get_default().get_default_seat()
        status = seat.grab(window, Gdk.SeatCapabilities.ALL, True,
                           None, None, None, None)
        self._grabbed = status == Gdk.GrabStatus.SUCCESS
        if self._grabbed:
            self.grab_add()
        elif attempt < 6:
            GLib.timeout_add(25, lambda: self._retry_grab(attempt + 1))

    def _retry_grab(self, attempt):
        if not self._grabbed and self.get_realized():
            self._grab(attempt)
        return GLib.SOURCE_REMOVE

    def _ungrab(self):
        if self._grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self.grab_remove()
            self._grabbed = False

    # -- animation ---------------------------------------------------------
    def _animate_in(self):
        self._t0 = now()
        self.set_opacity(0.0)
        if self._anim:
            GLib.source_remove(self._anim)
        self._anim = GLib.timeout_add(16, self._step)

    def _step(self):
        t = (now() - self._t0) / 0.13
        self.set_opacity(ease_out_cubic(t))
        if t >= 1.0:
            self._anim = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- input -------------------------------------------------------------
    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    def _on_button(self, _w, event):
        """Dismiss on a click outside us. Root coordinates only.

        With a seat grab GTK routes clicks on our *own* other windows -- the
        bar itself -- to this handler, but it does not translate the
        coordinates: event.x/y still refer to the window the click actually
        landed on. Testing those against our allocation therefore called a
        click on the bar "inside" whenever it happened to fall within our
        width, so the menu button swallowed the click instead of closing,
        and the menu could not be toggled shut. event.x_root/y_root are
        absolute and mean the same thing whichever window reported them.
        """
        if not self._want_grab:
            return False
        window = self.get_window()
        if window is None:
            return False
        _, origin_x, origin_y = window.get_origin()
        x = event.x_root - origin_x
        y = event.y_root - origin_y
        alloc = self.get_allocation()
        inside = (SHADOW <= x <= alloc.width - SHADOW
                  and SHADOW <= y <= alloc.height - SHADOW)
        if not inside:
            self.dismiss()
            return True
        return False

    def dismiss(self):
        self.emit("dismissed")
        if self.persistent:
            self._ungrab()
            self.hide()
        else:
            self.destroy()

    def prewarm(self):
        """Realise and draw off-screen so the first real open is instant.

        Realising this widget tree costs ~300ms; showing an already-realised
        one costs well under a millisecond.
        """
        self.move(-9000, -9000)
        self.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration()
        self.hide()

    def release_references(self):
        """Subclass hook: clear collections that hold child widgets.

        Plain widget attributes are swept automatically by `destroy`; this is
        for lists and dicts it cannot inspect.
        """

    def destroy(self):
        """Tear down, breaking the reference cycles GTK popups create.

        Connecting a bound method (or a lambda closing over `self`) to a
        signal on a widget this popup owns makes a cycle whose C-side edge
        the Python collector cannot traverse. Destroying the content subtree
        and then dropping every reference we hold to a child widget is what
        actually lets the tree finalise -- verified by weakref: without both
        steps, every popup ever opened stays alive.
        """
        if self._anim:
            GLib.source_remove(self._anim)
            self._anim = None
        self._ungrab()
        self.release_references()
        if self.content is not None:
            self.content.destroy()
            self.content = None
        for name, value in list(self.__dict__.items()):
            if isinstance(value, Gtk.Widget) and value is not self:
                self.__dict__[name] = None
        super().destroy()


# --------------------------------------------------------------------------
# small shared widget builders, so popups look consistent
# --------------------------------------------------------------------------

def css_provider(theme):
    """One stylesheet for all popup contents, keyed off the dock palette."""
    def h(key, alpha=None):
        r, g, b, a = theme[key]
        return f"rgba({int(r*255)},{int(g*255)},{int(b*255)},{alpha if alpha is not None else a:.3f})"

    css = f"""
    .td-popup label {{ color: {h('fg')}; }}
    .td-dim {{ color: {h('fg_dim')}; font-size: 90%; }}
    .td-title {{ font-weight: 600; }}
    .td-row {{ border-radius: 8px; padding: 5px 8px; }}
    .td-row:hover {{ background: {h('hover', 0.10)}; }}
    .td-row:selected, .td-selected, .td-row:checked {{
        background: {h('accent', 0.28)};
    }}
    .td-row:checked:hover {{ background: {h('accent', 0.36)}; }}
    list row:selected {{ background: {h('accent', 0.28)}; }}
    list row {{ border-radius: 8px; }}
    /* GTK themes commonly bold a selected row (Dracula's cell-row.css does:
       `row:selected {{ font: bold; }}`). Keyboard selection would then look
       heavier than mouse hover, which cannot bold, so the two read as
       different languages. The tinted background says "selected" on its own. */
    list row:selected label, .td-row:selected label,
    .td-row:checked label {{ font-weight: normal; }}
    /* Which pane the arrow keys drive. The inactive one keeps a quieter
       version of the same highlight rather than losing it, so you do not
       lose your place when you step sideways. */
    list.td-idle row:selected {{ background: {h('hover', 0.13)}; }}
    .td-cat.td-active:checked {{ background: {h('accent', 0.44)}; }}
    .td-sep {{ background: {h('sep')}; min-height: 1px; }}
    entry.td-search {{
        background: {h('hover', 0.09)};
        color: {h('fg')};
        border: 1px solid {h('popup_border')};
        border-radius: 9px;
        padding: 6px 9px;
        caret-color: {h('accent')};
    }}
    entry.td-search:focus {{ border-color: {h('accent', 0.65)}; }}
    entry.td-search placeholder, entry.td-search text placeholder {{
        color: {h('fg_faint')};
    }}
    entry.td-search image {{ color: {h('fg_dim')}; }}
    scale trough {{ background: {h('hover', 0.14)}; border-radius: 6px; min-height: 5px; }}
    scale highlight {{ background: {h('accent')}; border-radius: 6px; }}
    scale slider {{
        background: {h('fg')}; border-radius: 50%;
        min-width: 13px; min-height: 13px; margin: -5px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.4);
    }}
    button.td-btn {{
        background: {h('hover', 0.10)}; color: {h('fg')};
        border: 1px solid {h('popup_border')}; border-radius: 8px;
        padding: 5px 10px; text-shadow: none;
    }}
    button.td-btn:hover {{ background: {h('hover', 0.20)}; }}
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode())
    return provider


def label(text, cls=None, xalign=0.0, ellipsize=True):
    lbl = Gtk.Label(label=text)
    lbl.set_xalign(xalign)
    if ellipsize:
        lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
    if cls:
        lbl.get_style_context().add_class(cls)
    return lbl


def separator():
    sep = Gtk.Box()
    sep.get_style_context().add_class("td-sep")
    return sep
