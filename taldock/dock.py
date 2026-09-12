"""The dock window: layout, rendering and input dispatch."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (Gdk, GLib, Gtk, Pango,  # noqa: E402
                           PangoCairo)

from . import timing, x11
from .appmenu import AppMenuPopup
from .config import Config
from .ipc import ControlServer
from .popup import css_provider
from .launchers import LauncherZone
from .nm import NetworkMonitor
from .pulse import PulseAudio
from .sni import StatusNotifierHost
from .tabs import TabRegistry
from .theme import Theme
from .util import (ICONS, draw_shadow, ease_out_cubic, now, paint_surface,
                   rect, rgba, rounded_rect, with_alpha)
from .windowlist import WindowListPopup
from .windows import AppDatabase, WindowModel
from .widgets.audio import VOLUME_STEP, AudioItem
from .widgets.battery import BatteryItem
from .widgets.clock import ClockItem
from .widgets.network import NetworkItem
from .widgets.separator import SeparatorItem
from .widgets.sysmon import SysMonItem
from .widgets.tray import TrayItem

WIDGETS = {
    "sysmon": SysMonItem,
    "network": NetworkItem,
    "battery": BatteryItem,
    "audio": AudioItem,
    "tray": TrayItem,
    "clock": ClockItem,
    "sep": SeparatorItem,
}

ZONE_GAP = 14           # minimum gap between zones
AUTOHIDE_SEC = 0.18
REVEAL_PX = 2           # visible sliver when hidden


class Dock:
    def __init__(self, config_path=None):
        self.cfg = Config(config_path) if config_path else Config()
        self.theme = Theme(self.cfg)
        self.padding = float(self.cfg["padding"])
        self.icon_size = float(self.cfg["icon_size"])
        self.scale = 1

        # -- services
        self.appdb = AppDatabase()
        self.appdb.connect("changed", self._on_apps_changed)
        timing.mark("  AppDatabase")
        self.windows = WindowModel(self.appdb)
        timing.mark("  WindowModel (wnck force_update)")
        self.pulse = PulseAudio()
        timing.mark("  PulseAudio")
        self.network = NetworkMonitor()
        timing.mark("  NetworkMonitor")
        self.tray_host = StatusNotifierHost()
        timing.mark("  StatusNotifierHost")
        self.tabs = TabRegistry(self.windows) if self.cfg["browser_tabs"] else None
        self.control = ControlServer({
            "menu": self.toggle_app_menu,
            # Media keys: XFCE runs `taldock --volume-up` and friends, which
            # arrive here. See __main__.MEDIA_KEYS.
            "volume-up": lambda: self.adjust_volume(1),
            "volume-down": lambda: self.adjust_volume(-1),
            "volume-mute": self.toggle_mute,
            "mic-mute": self.toggle_mic_mute,
            "quit": Gtk.main_quit,
        })
        timing.mark("  TabRegistry + ControlServer")

        # -- geometry
        zoom = float(self.cfg["zoom_factor"]) if self.cfg["zoom"] else 1.0
        # Vertical metrics, top to bottom: padding, icon, indicator strip,
        # padding. `icon_bottom` is the baseline magnified icons grow from.
        self.indicator_strip = 7.0
        self.bar_height = self.padding * 2 + self.icon_size + self.indicator_strip
        self.headroom = int(math.ceil(self.icon_size * (zoom - 1.0))) + 6 if zoom > 1 else 0
        self._apply_vertical_metrics()

        # -- transient state
        self._menus = []
        self._window_list = None
        self._hover_icon = None
        self._hover_source = 0
        self._close_source = 0
        self._layout_valid = False
        self._bg_surface = None     # cached bar background
        self._bg_key = None
        self._hide_state = 0.0      # 0 = shown, 1 = hidden
        self._hide_anim = 0
        self._hide_t0 = 0.0
        self._hide_from = 0.0
        self._hidden = False
        self._pointer_inside = False

        self._build_window()
        timing.mark("  window built")
        self.launchers = LauncherZone(self)
        timing.mark("  LauncherZone")
        self.status_items = []
        self._build_status_items()
        timing.mark("  status widgets")
        self.menu_width = self._measure_menu()
        self.update_geometry()
        timing.mark("  geometry")
        if self.tabs is not None:
            self.tabs.connect("changed", lambda *_a: self.queue_draw_center())

    # ------------------------------------------------------------------
    # window setup
    # ------------------------------------------------------------------
    def _build_window(self):
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("taldock")
        self.window.set_wmclass("taldock", "Taldock")
        self.window.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_keep_above(True)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.stick()
        self.window.set_app_paintable(True)

        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.window.set_visual(visual)
        # One screen-wide stylesheet for every popup. Adding a provider per
        # popup would stack them up forever and slow every style lookup.
        Gtk.StyleContext.add_provider_for_screen(
            screen, css_provider(self.theme),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        screen.connect("monitors-changed", lambda *_a: self.update_geometry())
        screen.connect("size-changed", lambda *_a: self.update_geometry())

        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK)
        self.area.connect("draw", self._on_draw)
        self.area.connect("motion-notify-event", self._on_motion)
        self.area.connect("button-press-event", self._on_button_press)
        self.area.connect("button-release-event", self._on_button_release)
        self.area.connect("scroll-event", self._on_scroll)
        self.area.connect("leave-notify-event", self._on_leave)
        self.area.connect("enter-notify-event", self._on_enter)
        self.area.set_has_tooltip(True)
        self.area.connect("query-tooltip", self._on_query_tooltip)
        self.window.add(self.area)
        self.window.connect("destroy", lambda *_a: Gtk.main_quit())

    def _build_status_items(self):
        for item in self.status_items:
            item.destroy()
        self.status_items = []
        for name in self.cfg["widgets"]:
            factory = WIDGETS.get(name)
            if factory is None:
                print(f"taldock: unknown widget {name!r}")
                continue
            self.status_items.append(factory(self))

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def _apply_vertical_metrics(self):
        """Place the icon row and indicator strip, all relative to the bar top.

        Icons keep the edge nearest the screen border fixed and magnify away
        from it, so the row always grows into the dock's headroom.
        """
        if self.at_bottom():
            self.icon_top = self.padding
            self.icon_bottom = self.padding + self.icon_size
            self.indicator_y = self.icon_bottom + 3.0
            self.grows_up = True
        else:
            self.icon_top = self.padding + self.indicator_strip
            self.icon_bottom = self.icon_top + self.icon_size
            self.indicator_y = self.padding + 1.0
            self.grows_up = False

    def monitor(self):
        display = Gdk.Display.get_default()
        want = self.cfg["monitor"]
        if want and want != "primary":
            for index in range(display.get_n_monitors()):
                mon = display.get_monitor(index)
                if mon.get_model() == want:
                    return mon
        return display.get_primary_monitor() or display.get_monitor(0)

    def monitor_geometry(self):
        return self.monitor().get_geometry()

    def at_bottom(self):
        return self.cfg["position"] != "top"

    def bar_rect_root(self):
        """The drawn bar's rectangle in root coordinates."""
        geo = self.monitor_geometry()
        margin = self.cfg["margin"]
        side = self.cfg["side_margin"]
        width = geo.width - side * 2
        offset = int(self._hide_state * (self.bar_height + margin - REVEAL_PX))
        if self.at_bottom():
            y = geo.y + geo.height - margin - self.bar_height + offset
        else:
            y = geo.y + margin - offset
        return rect(geo.x + side, y, width, int(self.bar_height))

    def root_x(self):
        return self.bar_rect_root().x

    def update_geometry(self):
        geo = self.monitor_geometry()
        self.scale = self.monitor().get_scale_factor()
        margin = self.cfg["margin"]
        side = self.cfg["side_margin"]
        width = geo.width - side * 2
        height = int(self.bar_height + self.headroom)

        bar = self.bar_rect_root()
        # The window is taller than the bar so magnified icons can overflow.
        win_y = bar.y - self.headroom if self.at_bottom() else bar.y
        self.window.set_size_request(width, height)
        self.window.resize(width, height)
        self.window.move(bar.x, win_y)

        self._apply_input_region()
        self._apply_strut()
        self.invalidate_layout()

    def _apply_input_region(self):
        """Only the bar itself takes input; the zoom headroom clicks through."""
        gdk_window = self.window.get_window()
        if gdk_window is None:
            return
        top = self.headroom if self.at_bottom() else 0
        width = self.window.get_allocated_width() or self.area.get_allocated_width()
        if width <= 1:
            return
        import cairo
        region = cairo.Region(cairo.RectangleInt(
            0, int(top), int(width), int(self.bar_height)))
        gdk_window.input_shape_combine_region(region, 0, 0)

    def _apply_strut(self):
        gdk_window = self.window.get_window()
        if gdk_window is None:
            return
        xid = gdk_window.get_xid()
        geo = self.monitor_geometry()
        if not self.cfg["reserve_space"] or self.cfg["autohide"] != "none":
            x11.clear_strut(xid)
            return
        thickness = int(self.bar_height + self.cfg["margin"])
        if self.at_bottom():
            # Struts are measured from the screen edge, not the monitor's.
            screen = self.window.get_screen()
            thickness += screen.get_height() - (geo.y + geo.height)
        else:
            thickness += geo.y
        x11.set_strut(xid, self.cfg["position"], thickness,
                      geo.x, geo.x + geo.width - 1,
                      self.window.get_screen().get_height(),
                      self.window.get_screen().get_width())

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def invalidate_layout(self):
        self._layout_valid = False
        self._bg_key = None
        self.area.queue_draw()

    def invalidate_status_layout(self):
        self.invalidate_layout()

    def _layout(self):
        width = self.area.get_allocated_width()
        if width <= 1:
            width = self.window.get_allocated_width()

        # Right zone: measure everything, drop redundant dividers, then
        # place the survivors right to left.
        for item in self.status_items:
            item.w = float(item.measure(self.bar_height))
        self._collapse_separators()

        cursor = width - self.padding
        for item in reversed(self.status_items):
            if item.w <= 0:
                item.x = cursor
                continue
            cursor -= item.w
            item.x = cursor
        status_left = cursor

        # Left zone: the menu button.
        self.menu_rect = (self.padding, self.menu_width)
        centre_left = self.padding + self.menu_width + ZONE_GAP
        centre_right = status_left - ZONE_GAP

        self.launchers.x = centre_left
        self.launchers.width = max(0.0, centre_right - centre_left)
        # Centre the launchers on the bar itself, not on the leftover gap
        # between the two side zones, which are very different widths.
        self.launchers.preferred_center = width / 2.0
        self._layout_valid = True

    def _collapse_separators(self):
        """Hide dividers that would sit at an edge or beside another divider.

        A status widget can measure zero -- an empty system tray, a machine
        with no battery -- and without this the dividers on either side of it
        end up stacked together with nothing in between.
        """
        pending = None
        seen_item = False
        for item in self.status_items:
            if isinstance(item, SeparatorItem):
                item.w = 0.0
                if seen_item:
                    pending = item      # keep only if a real item follows
                continue
            if item.w <= 0:
                continue
            if pending is not None:
                pending.w = float(pending.measure(self.bar_height))
                pending = None
            seen_item = True

    def _ensure_layout(self):
        if not self._layout_valid:
            self._layout()

    def _measure_menu(self):
        width = self.icon_size + 16
        if self.cfg["show_menu_label"]:
            width += self.text_width(self.cfg["menu_label"], 10.0, bold=True) + 8
        return width

    # ------------------------------------------------------------------
    # text helpers
    # ------------------------------------------------------------------
    def pango_layout(self, text, size=9.0, bold=False):
        layout = self.area.create_pango_layout(text)
        desc = Pango.FontDescription(self.theme.font)
        desc.set_size(int(size * Pango.SCALE))
        if bold:
            desc.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(desc)
        return layout

    def text_width(self, text, size=9.0, bold=False):
        return self.pango_layout(text, size, bold).get_pixel_size().width

    @staticmethod
    def show_layout(cr, layout):
        PangoCairo.show_layout(cr, layout)

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------
    def bar_top(self):
        return self.headroom if self.at_bottom() else 0

    def _on_draw(self, _widget, cr):
        timing.mark_once("first draw")
        self._ensure_layout()
        width = self.area.get_allocated_width()
        top = self.bar_top()
        height = self.bar_height

        cr.set_operator(1)              # SOURCE: start from fully transparent
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)              # OVER

        self._draw_background(cr, 0, top, width, height)

        cr.save()
        cr.translate(0, top)
        self._draw_menu_button(cr, height)
        cr.restore()

        # Launchers may overflow upward into the headroom, so no clip here.
        cr.save()
        cr.translate(0, top)
        self.launchers.draw(cr, height)
        cr.restore()

        for item in self.status_items:
            if item.w <= 0:
                continue
            cr.save()
            cr.translate(item.x, top)
            cr.rectangle(0, 0, item.w, height)
            cr.clip()
            item.draw(cr, item.w, height)
            cr.restore()
        return False

    def _draw_background(self, cr, x, y, width, height):
        """Blit the cached bar background.

        The gradient, layered shadow and border never change between frames,
        so they are rendered once and reused. This keeps a status widget's
        two-second tick from repainting 1900px of gradient.
        """
        import cairo
        key = (int(width), int(height), self.cfg["radius"], self.at_bottom(),
               id(self.theme))
        if self._bg_key != key or self._bg_surface is None:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                         int(width), int(height))
            self._render_background(cairo.Context(surface), width, height)
            self._bg_surface = surface
            self._bg_key = key
        cr.set_source_surface(self._bg_surface, x, y)
        cr.paint()

    def _render_background(self, cr, width, height):
        radius = float(self.cfg["radius"])
        draw_shadow(cr, 1, 0, width - 2, height, radius, 7,
                    self.theme["shadow"])

        gradient = self._gradient(0, height)
        rounded_rect(cr, 0, 0, width, height, radius)
        cr.set_source(gradient)
        cr.fill()

        # Hairline border plus a brighter sheen along the top edge, which is
        # what gives the bar its raised, glassy look.
        rgba(cr, self.theme["border"])
        cr.set_line_width(1.0)
        rounded_rect(cr, 0.5, 0.5, width - 1, height - 1, radius)
        cr.stroke()

        cr.save()
        rounded_rect(cr, 1, 1, width - 2, height - 2, radius - 1)
        cr.clip()
        rgba(cr, self.theme["sheen"])
        cr.set_line_width(1.4)
        cr.move_to(radius * 0.6, 1.0)
        cr.line_to(width - radius * 0.6, 1.0)
        cr.stroke()
        cr.restore()

    def _gradient(self, y, height):
        import cairo
        gradient = cairo.LinearGradient(0, y, 0, y + height)
        top_colour = self.theme["bg_hi"]
        bottom_colour = self.theme["bg"]
        if not self.at_bottom():
            top_colour, bottom_colour = bottom_colour, top_colour
        gradient.add_color_stop_rgba(0.0, *top_colour)
        gradient.add_color_stop_rgba(1.0, *bottom_colour)
        return gradient

    def _draw_menu_button(self, cr, height):
        x, width = self.menu_rect
        hovering = self._hover_zone == "menu"
        if hovering or self._menu_popup_open():
            rgba(cr, self.theme["active"] if self._menu_popup_open()
                 else self.theme["hover"])
            rounded_rect(cr, x, 4, width, height - 8, 10)
            cr.fill()

        size = int(self.icon_size * 0.68)
        surf = ICONS.surface(self.cfg["menu_icon"], size, self.scale,
                             fallback="applications-system")
        icon_x = x + 8
        if not self.cfg["show_menu_label"]:
            icon_x = x + (width - size) / 2
        paint_surface(cr, surf, icon_x, (height - size) / 2, size, self.scale)

        if self.cfg["show_menu_label"]:
            layout = self.pango_layout(self.cfg["menu_label"], 10.0, bold=True)
            sz = layout.get_pixel_size()
            rgba(cr, self.theme["fg"])
            cr.move_to(icon_x + size + 8, (height - sz.height) / 2)
            self.show_layout(cr, layout)

    def queue_draw_center(self):
        """Invalidate only the icon row, full height so the headroom repaints.

        The launcher zone spans most of the bar but the icons occupy a few
        hundred pixels of it, and this runs every frame while magnifying.
        The extent is last frame's, so it is padded to cover the row's growth
        since then.
        """
        if not self._layout_valid:
            self.area.queue_draw()
            return
        height = self.area.get_allocated_height()
        extent = self.launchers.extent
        if extent is None:
            self.area.queue_draw_area(int(self.launchers.x) - 2, 0,
                                      int(self.launchers.width) + 4, height)
            return
        pad = self.icon_size * 2.0
        x0 = max(0, int(extent[0] - pad))
        x1 = int(extent[1] + pad)
        self.area.queue_draw_area(x0, 0, x1 - x0, height)

    def queue_draw_status(self):
        self.area.queue_draw()

    def queue_draw_item(self, item):
        """Invalidate one status cell -- the common case for ticking widgets."""
        if not self._layout_valid or item.w <= 0:
            self.area.queue_draw()
            return
        self.area.queue_draw_area(int(item.x) - 1, int(self.bar_top()),
                                  int(item.w) + 2, int(self.bar_height))

    # ------------------------------------------------------------------
    # input
    # ------------------------------------------------------------------
    _hover_zone = None

    def _zone_at(self, x, y):
        self._ensure_layout()
        menu_x, menu_w = self.menu_rect
        if menu_x <= x <= menu_x + menu_w:
            return "menu", None
        for item in self.status_items:
            if item.interactive and item.w > 0 and item.x <= x <= item.x + item.w:
                return "status", item
        if self.launchers.x <= x <= self.launchers.x + self.launchers.width:
            return "launchers", None
        return None, None

    def _widget_y(self, event_y):
        return event_y - self.bar_top()

    def _on_motion(self, _widget, event):
        y = self._widget_y(event.y)
        zone, item = self._zone_at(event.x, y)
        if zone != self._hover_zone:
            if self._hover_zone == "launchers":
                self.launchers.on_leave()
            self._hover_zone = zone
            self.area.queue_draw()

        for status_item in self.status_items:
            hovering = status_item is item
            if status_item.hover != hovering:
                status_item.hover = hovering
                self.area.queue_draw()
            if hovering and isinstance(status_item, TrayItem):
                status_item.set_hover_x(event.x - status_item.x)

        if zone == "launchers":
            if self.launchers.on_drag(event.x):
                return True
            self.launchers.on_motion(event.x, y)
        return True

    def _on_enter(self, _widget, _event):
        self._pointer_inside = True
        if self.cfg["autohide"] != "none":
            self.reveal()
        return False

    def _on_leave(self, _widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._pointer_inside = False
        self._hover_zone = None
        self.launchers.on_leave()
        for item in self.status_items:
            item.hover = False
            if isinstance(item, TrayItem):
                item.set_hover_x(None)
        self.area.queue_draw()
        if self.cfg["autohide"] != "none":
            self.schedule_hide()
        return False

    def _on_button_press(self, _widget, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False           # ignore the synthetic 2BUTTON_PRESS
        y = self._widget_y(event.y)
        zone, item = self._zone_at(event.x, y)
        if zone == "menu":
            self.toggle_app_menu()
            return True
        if zone == "status" and item is not None:
            item.pressed = True
            self.area.queue_draw()
            return True
        if zone == "launchers":
            return self.launchers.on_press(event.button, event.x, y, event)
        return False

    def _on_button_release(self, _widget, event):
        y = self._widget_y(event.y)
        zone, item = self._zone_at(event.x, y)
        for status_item in self.status_items:
            if status_item.pressed:
                status_item.pressed = False
                self.area.queue_draw()
        if zone == "status" and item is not None:
            return item.on_click(event.button, event.x - item.x, y, event)
        if zone == "launchers":
            return self.launchers.on_release(event.button, event.x, y, event)
        return False

    def _on_scroll(self, _widget, event):
        y = self._widget_y(event.y)
        zone, item = self._zone_at(event.x, y)
        direction = 0
        if event.direction == Gdk.ScrollDirection.UP:
            direction = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            direction = -1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            direction = -1 if event.delta_y > 0 else 1
        if direction == 0:
            return False
        if zone == "status" and item is not None:
            return item.on_scroll(direction, event)
        if zone == "launchers":
            return self.launchers.on_scroll(direction, event.x, y, event)
        return False

    def _on_query_tooltip(self, _widget, x, y, _keyboard, tooltip):
        zone, item = self._zone_at(x, self._widget_y(y))
        text = None
        if zone == "menu":
            text = "Applications"
        elif zone == "status" and item is not None:
            text = item.tooltip()
        elif zone == "launchers" and not self.cfg["hover_previews"]:
            index = self.launchers.index_at(x, y)
            if index >= 0:
                text = self.launchers.icons[index].name
        if not text:
            return False
        tooltip.set_text(text)
        return True

    # ------------------------------------------------------------------
    # popups
    # ------------------------------------------------------------------
    def keep_menu(self, menu):
        """Hold a reference to a GtkMenu for as long as it is on screen."""
        self._menus.append(menu)
        menu.connect("deactivate",
                     lambda m: GLib.idle_add(self._menus.remove, m)
                     if m in self._menus else None)

    def _menu_popup_open(self):
        menu = getattr(self, "_app_menu", None)
        return menu is not None and menu.get_visible()

    def app_menu(self):
        """The one applications menu, built on first use."""
        if getattr(self, "_app_menu", None) is None:
            self._app_menu = AppMenuPopup(self)
            self._app_menu.connect("dismissed", self._on_app_menu_closed)
        return self._app_menu

    def _on_apps_changed(self, *_a):
        """An application was installed or removed."""
        menu = getattr(self, "_app_menu", None)
        if menu is not None:
            menu.refresh_apps()

    def prewarm_app_menu(self):
        """Pay the menu's build cost at startup, not on the first Super press."""
        timing.mark("app menu prewarm: start")
        self.app_menu().prewarm()
        timing.mark("app menu prewarm: done")
        return GLib.SOURCE_REMOVE

    def toggle_app_menu(self):
        menu = self.app_menu()
        if menu.get_visible():
            menu.dismiss()
            return
        menu.reset()
        menu.open_at(self.root_x() + self.padding, align="start")
        self.area.queue_draw()

    def _on_app_menu_closed(self, *_a):
        self.area.queue_draw()

    def item_center_root(self, item):
        return self.root_x() + item.x + item.w / 2

    # ------------------------------------------------------------------
    # volume, from the media keys
    # ------------------------------------------------------------------
    def _audio_item(self):
        """The volume widget, or None when it is not in `widgets`."""
        for item in self.status_items:
            if isinstance(item, AudioItem):
                return item
        return None

    def adjust_volume(self, direction):
        item = self._audio_item()
        if item is not None:
            item.nudge(direction)       # also flashes the level bar
        else:
            self.pulse.step_volume(VOLUME_STEP * direction)

    def toggle_mute(self):
        item = self._audio_item()
        if item is not None:
            item.toggle_mute()
        else:
            self.pulse.toggle_mute()

    def toggle_mic_mute(self):
        self.pulse.set_mic_mute(not self.pulse.mic_mute)

    # -- hover window list -------------------------------------------------
    def on_launcher_hover(self, icon):
        if icon is self._hover_icon:
            return
        self._hover_icon = icon
        self.cancel_window_list_close()
        if self._hover_source:
            GLib.source_remove(self._hover_source)
            self._hover_source = 0
        if icon is None:
            self.schedule_window_list_close()
            return
        if not self.cfg["hover_previews"] or not icon.running:
            self.close_window_list()
            return
        self._hover_source = GLib.timeout_add(
            self.cfg["hover_delay_ms"], self._open_window_list, icon)

    def _open_window_list(self, icon):
        self._hover_source = 0
        if self._hover_icon is not icon or not icon.running:
            return GLib.SOURCE_REMOVE
        self.close_window_list()
        popup = WindowListPopup(self, icon)
        self._window_list = popup
        popup.connect("destroy", self._on_window_list_closed)
        popup.open_at(self.launchers.icon_center_root(icon))
        return GLib.SOURCE_REMOVE

    def show_window_list(self, icon, sticky=False):
        self.close_window_list()
        popup = WindowListPopup(self, icon, sticky=sticky)
        self._window_list = popup
        popup.connect("destroy", self._on_window_list_closed)
        popup.open_at(self.launchers.icon_center_root(icon))

    def _on_window_list_closed(self, *_a):
        self._window_list = None

    def schedule_window_list_close(self):
        if self._close_source or self._window_list is None:
            return
        self._close_source = GLib.timeout_add(260, self._do_close_window_list)

    def cancel_window_list_close(self):
        if self._close_source:
            GLib.source_remove(self._close_source)
            self._close_source = 0

    def _do_close_window_list(self):
        self._close_source = 0
        self.close_window_list()
        return GLib.SOURCE_REMOVE

    def close_window_list(self):
        if self._window_list is not None:
            self._window_list.destroy()
            self._window_list = None

    # ------------------------------------------------------------------
    # autohide
    # ------------------------------------------------------------------
    def reveal(self):
        self._set_hidden(False)

    def schedule_hide(self):
        if self.cfg["autohide"] == "none":
            return
        GLib.timeout_add(400, self._maybe_hide)

    def _maybe_hide(self):
        if not self._pointer_inside and self._window_list is None \
                and not self._menu_popup_open():
            self._set_hidden(True)
        return GLib.SOURCE_REMOVE

    def _set_hidden(self, hidden):
        if hidden == self._hidden:
            return
        self._hidden = hidden
        self._hide_from = self._hide_state
        self._hide_t0 = now()
        if not self._hide_anim:
            self._hide_anim = GLib.timeout_add(16, self._hide_tick)

    def _hide_tick(self):
        target = 1.0 if self._hidden else 0.0
        t = (now() - self._hide_t0) / AUTOHIDE_SEC
        self._hide_state = self._hide_from + (target - self._hide_from) * ease_out_cubic(t)
        bar = self.bar_rect_root()
        self.window.move(bar.x, bar.y - self.headroom if self.at_bottom() else bar.y)
        if t >= 1.0:
            self._hide_state = target
            self._hide_anim = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------
    def run(self):
        self.window.show_all()
        timing.mark("window shown")
        # Struts and the input region need a realized X window.
        GLib.idle_add(self._post_show)
        Gtk.main()

    def _post_show(self):
        self._apply_input_region()
        self._apply_strut()
        self.invalidate_layout()
        timing.mark("struts + input region")
        # Build the applications menu once the dock itself is up, so the
        # first Super press does not pay for it.
        GLib.timeout_add(1200, self.prewarm_app_menu)
        return GLib.SOURCE_REMOVE

    def shutdown(self):
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            x11.clear_strut(gdk_window.get_xid())
        if self.tabs is not None:
            self.tabs.shutdown()
        if self.control is not None:
            self.control.shutdown()
