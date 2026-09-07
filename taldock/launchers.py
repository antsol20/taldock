"""The centre zone: pinned launchers, running apps, indicators, magnification."""
from __future__ import annotations

import math

from gi.repository import Gdk, Gio, GLib, Gtk

from .util import (ICONS, ease_out_back, ease_out_cubic, now, paint_surface,
                   rgba, rounded_rect, with_alpha)

GAP = 10.0              # horizontal gap between icon slots
BOUNCE_SEC = 0.62
APPEAR_SEC = 0.28
ZOOM_ENVELOPE = 0.11    # seconds for magnification to ease in/out
POINTER_TAU = 0.035     # pointer-following time constant, seconds


def _normalise(label):
    """Fold a menu label for comparison: no mnemonics, no case, no spacing."""
    return "".join(ch for ch in label.lower() if ch.isalnum())


class DockIcon:
    """One launcher slot: a pinned app, a running app, or both."""

    def __init__(self, zone, key, appinfo=None, pinned=False):
        self.zone = zone
        self.key = key
        self.appinfo = appinfo
        self.pinned = pinned
        self.windows = []
        # layout, recomputed every frame
        self.x = 0.0
        self.scale = 1.0
        # animation clocks
        self.bounce_t0 = 0.0
        self.appear_t0 = now()
        self.urgent = False

    # -- presentation ------------------------------------------------------
    @property
    def name(self):
        if self.appinfo is not None:
            return self.appinfo.get_name()
        if self.windows:
            return (self.windows[0].get_class_group_name()
                    or self.windows[0].get_name())
        return self.key

    @property
    def icon_source(self):
        if self.appinfo is not None:
            icon = self.appinfo.get_icon()
            if icon is not None:
                return icon
        return None

    def surface(self, size, scale):
        src = self.icon_source
        if src is not None:
            surf = ICONS.surface(src, size, scale, fallback=None)
            if surf is not None:
                return surf
        # Fall back to the window's own _NET_WM_ICON.
        if self.windows:
            pixbuf = self.windows[0].get_icon()
            if pixbuf is not None:
                return ICONS.surface_from_pixbuf(
                    pixbuf, size, scale, key=f"win:{self.key}")
        return ICONS.surface("application-x-executable", size, scale)

    @property
    def running(self):
        return bool(self.windows)

    def window_count(self):
        return len(self.windows)

    # -- actions -----------------------------------------------------------
    def launch(self, uris=None, action=None):
        self.bounce_t0 = now()
        self.zone.start_animation()
        if self.appinfo is None:
            return
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
            if action:
                self.appinfo.launch_action(action, ctx)
            elif uris:
                self.appinfo.launch_uris(uris, ctx)
            else:
                self.appinfo.launch([], ctx)
        except GLib.Error as exc:
            print(f"taldock: launch failed for {self.key}: {exc}")


class LauncherZone:
    """Owns icon state, layout and hit-testing for the dock's centre."""

    def __init__(self, dock):
        self.dock = dock
        self.cfg = dock.cfg
        self.theme = dock.theme
        self.model = dock.windows
        self.icons = []
        self.x = 0.0
        self.width = 0.0
        self.preferred_center = None
        # pointer_x is smoothed and drives layout; _pointer_target is the raw
        # position from the last motion event.
        self.pointer_x = None
        self._pointer_target = None
        self._hovering = False
        self.extent = None          # (left, right) of the drawn row
        self.hover_index = -1
        self.pressed_index = -1
        self._zoom_amount = 0.0
        self._zoom_from = 0.0
        self._zoom_t0 = 0.0
        self._tick_id = 0
        self._last_frame = 0.0
        self._drag_index = -1
        self._drag_offset = 0.0
        self._drag_x = 0.0

        self.icon_size = float(self.cfg["icon_size"])
        self.zoom_factor = float(self.cfg["zoom_factor"]) if self.cfg["zoom"] else 1.0
        self.zoom_range = float(self.cfg["zoom_range"])
        # Cache icons at their largest drawn size so magnification stays sharp.
        self.render_size = int(math.ceil(self.icon_size * max(1.0, self.zoom_factor)))

        self.model.connect("changed", lambda *_a: self.rebuild())
        self.model.connect("window-updated", lambda *_a: self.dock.queue_draw_center())
        self.rebuild()

    # -- icon list ---------------------------------------------------------
    def rebuild(self):
        """Reconcile pinned config + running windows into the icon list."""
        previous = {icon.key: icon for icon in self.icons}
        icons = []
        seen = set()

        for entry in self.cfg["launchers"]:
            info = self.dock.appdb.get(entry)
            if info is None:
                continue          # app not installed; keep the config entry
            key = info.get_id()
            if key in seen:
                continue
            icon = previous.get(key) or DockIcon(self, key, info, pinned=True)
            icon.appinfo = info
            icon.pinned = True
            icon.windows = self.model.windows_for(key)
            icons.append(icon)
            seen.add(key)

        if self.cfg["show_unpinned"]:
            for key, windows in self.model.groups.items():
                if key in seen or not windows:
                    continue
                icon = previous.get(key) or DockIcon(
                    self, key, self.model.appinfo.get(key), pinned=False)
                icon.windows = windows
                icons.append(icon)
                seen.add(key)

        for icon in icons:
            icon.urgent = any(w.needs_attention() for w in icon.windows)

        self.icons = icons
        self.dock.invalidate_layout()

    def pinned_keys(self):
        return [i.key for i in self.icons if i.pinned]

    def save_pinned(self):
        self.cfg["launchers"] = self.pinned_keys()
        self.cfg.save()

    def toggle_pin(self, icon):
        if icon.pinned and icon.running:
            icon.pinned = False
        elif icon.pinned:
            self.icons.remove(icon)
        else:
            icon.pinned = True
        self.save_pinned()
        self.rebuild()

    # -- layout ------------------------------------------------------------
    def natural_width(self):
        """Un-magnified width, used to reserve space in the bar."""
        count = len(self.icons)
        if not count:
            return 0.0
        return count * self.icon_size + (count - 1) * GAP

    def _scale_for(self, distance):
        """Cosine falloff magnification, as a factor >= 1."""
        if self.zoom_factor <= 1.0 or self._zoom_amount <= 0.0:
            return 1.0
        span = self.zoom_range * (self.icon_size + GAP)
        if distance >= span:
            return 1.0
        falloff = 0.5 * (1.0 + math.cos(math.pi * distance / span))
        return 1.0 + (self.zoom_factor - 1.0) * falloff * self._zoom_amount

    def layout(self):
        """Assign each icon an x and a scale for this frame."""
        count = len(self.icons)
        if not count:
            return
        slot = self.icon_size + GAP
        natural = self.natural_width()
        if self.preferred_center is not None:
            base_left = self.preferred_center - natural / 2.0
        else:
            base_left = self.x + (self.width - natural) / 2.0
        # Never let the row spill into the menu or status zones.
        if natural <= self.width:
            base_left = max(self.x, min(base_left, self.x + self.width - natural))
        else:
            base_left = self.x

        if self.pointer_x is None or self._zoom_amount <= 0.001:
            for index, icon in enumerate(self.icons):
                icon.scale = 1.0
                icon.x = base_left + index * slot
            self.extent = (base_left, base_left + natural)
            return

        # Scales come from the resting centres, so the falloff stays stable
        # while the pointer moves.
        scales = []
        for index in range(count):
            centre = base_left + index * slot + self.icon_size / 2.0
            scales.append(self._scale_for(abs(self.pointer_x - centre)))

        widths = [self.icon_size * s for s in scales]
        total = sum(widths) + GAP * (count - 1)

        # Grow the row about the pointer, preserving how far along the row the
        # pointer sits. Every term here is continuous in pointer_x, so the row
        # never jumps -- the previous version anchored to the nearest icon,
        # which snapped each time a different icon became nearest.
        # The pointer is clamped to the resting row so that a pointer out in
        # the empty part of the zone leaves the row where it is.
        grip = max(base_left, min(self.pointer_x, base_left + natural))
        frac = (grip - base_left) / natural if natural > 0 else 0.5
        left = grip - frac * total
        # Keep the magnified row inside the zone.
        left = max(self.x, min(left, self.x + self.width - total))

        cursor = left
        for index, icon in enumerate(self.icons):
            icon.scale = scales[index]
            icon.x = cursor
            cursor += widths[index] + GAP
        self.extent = (left, left + total)

    # -- animation ---------------------------------------------------------
    def start_animation(self):
        """Animate off the widget's frame clock.

        A plain 16ms timeout drifts against the compositor and shows up as
        stutter; the frame clock is what GTK paints on.
        """
        if self._tick_id:
            return
        self._last_frame = now()
        self._tick_id = self.dock.area.add_tick_callback(self._on_frame)

    def _on_frame(self, _widget, _clock):
        moment = now()
        dt = min(0.1, max(0.001, moment - self._last_frame))
        self._last_frame = moment
        active = False

        target = 1.0 if self._hovering else 0.0
        if abs(self._zoom_amount - target) > 0.002:
            elapsed = (moment - self._zoom_t0) / ZOOM_ENVELOPE
            self._zoom_amount = self._zoom_from + (target - self._zoom_from) \
                * ease_out_cubic(min(1.0, elapsed))
            active = True
        else:
            self._zoom_amount = target
            if not self._hovering:
                # Fully collapsed: only now is it safe to forget the pointer,
                # so the row eases back to rest instead of snapping on leave.
                self.pointer_x = None

        # Chase the pointer with an exponential ease. Motion events arrive
        # unevenly and coalesced; following them raw is what made the
        # magnification look jerky.
        if self._pointer_target is not None and self.pointer_x is not None:
            delta = self._pointer_target - self.pointer_x
            if abs(delta) > 0.25:
                self.pointer_x += delta * (1.0 - math.exp(-dt / POINTER_TAU))
                active = True
            else:
                self.pointer_x = self._pointer_target

        for icon in self.icons:
            if icon.bounce_t0:
                if moment - icon.bounce_t0 < BOUNCE_SEC:
                    active = True
                else:
                    icon.bounce_t0 = 0.0
            if icon.appear_t0:
                if moment - icon.appear_t0 < APPEAR_SEC:
                    active = True
                else:
                    icon.appear_t0 = 0.0
        if any(i.urgent for i in self.icons):
            active = True

        self.dock.queue_draw_center()
        if not active:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _begin_zoom(self):
        self._zoom_from = self._zoom_amount
        self._zoom_t0 = now()
        self.start_animation()

    # -- painting ----------------------------------------------------------
    def draw(self, cr, height):
        self.layout()
        moment = now()
        for index, icon in enumerate(self.icons):
            self._draw_icon(cr, icon, index, height, moment)

    def _draw_icon(self, cr, icon, index, height, moment):
        size = self.icon_size * icon.scale
        up = self.dock.grows_up
        x = icon.x

        def place(current):
            # The edge facing the screen border stays put; the icon grows in.
            return (self.dock.icon_bottom - current if up
                    else self.dock.icon_top)

        y = place(size)
        alpha = 1.0
        if icon.appear_t0:
            t = (moment - icon.appear_t0) / APPEAR_SEC
            alpha = ease_out_cubic(t)
            size *= 0.6 + 0.4 * ease_out_back(t)
            y = place(size)

        if icon.bounce_t0:
            t = (moment - icon.bounce_t0) / BOUNCE_SEC
            # Two decaying hops, away from the screen edge.
            hop = abs(math.sin(t * math.pi * 2.0)) * (1.0 - t) ** 1.5
            y += (-1 if up else 1) * hop * self.icon_size * 0.42

        if icon.urgent:
            pulse = 0.5 + 0.5 * math.sin(moment * 4.0)
            rgba(cr, with_alpha(self.theme["warn"], 0.16 + 0.22 * pulse))
            rounded_rect(cr, x - 5, y - 5, size + 10, size + 10, size * 0.32)
            cr.fill()
        elif index == self.hover_index and self._zoom_amount < 0.5:
            rgba(cr, self.theme["hover"])
            rounded_rect(cr, x - 4, y - 4, size + 8, size + 8, size * 0.3)
            cr.fill()

        if index == self.pressed_index:
            # Shrink about the icon's centre, so the nudge reads the same
            # whichever edge the dock is docked to.
            shrunk = size * 0.92
            x += (size - shrunk) / 2
            y += (size - shrunk) / 2
            size = shrunk

        surf = icon.surface(self.render_size, self.dock.scale)
        paint_surface(cr, surf, x, y, size, self.dock.scale, alpha)

        if icon.running:
            self._draw_indicator(cr, icon, x + size / 2, height, alpha)

    def _draw_indicator(self, cr, icon, cx, height, alpha):
        """A pill under the icon; its length encodes the window count."""
        count = icon.window_count()
        active = self.model.is_active_group(icon.key)
        width = 4.0 if count == 1 else (11.0 if count == 2 else 16.0)
        thickness = 3.0
        y = self.dock.indicator_y
        colour = self.theme["accent"] if active else self.theme["fg_dim"]
        opacity = (1.0 if active else 0.62) * alpha

        if active:
            rgba(cr, with_alpha(colour, 0.30 * alpha))
            rounded_rect(cr, cx - width / 2 - 2.5, y - 2.0,
                         width + 5, thickness + 4, (thickness + 4) / 2)
            cr.fill()
        rgba(cr, with_alpha(colour, opacity))
        rounded_rect(cr, cx - width / 2, y, width, thickness, thickness / 2)
        cr.fill()

    # -- hit testing -------------------------------------------------------
    def index_at(self, x, y):
        for index, icon in enumerate(self.icons):
            size = self.icon_size * icon.scale
            if icon.x - GAP / 2 <= x <= icon.x + size + GAP / 2:
                return index
        return -1

    def icon_center_root(self, icon):
        return self.dock.root_x() + icon.x + self.icon_size * icon.scale / 2

    # -- input -------------------------------------------------------------
    def on_motion(self, x, y):
        if not self._hovering:
            self._hovering = True
            # Start from where the pointer actually entered rather than
            # sliding in from a stale position.
            self.pointer_x = x
            self._begin_zoom()
        self._pointer_target = x
        self.start_animation()
        index = self.index_at(x, y)
        if index != self.hover_index:
            self.hover_index = index
            self.dock.on_launcher_hover(self.icons[index] if index >= 0 else None)
        self.dock.queue_draw_center()

    def on_leave(self):
        if not self._hovering:
            return
        self._hovering = False
        self._pointer_target = None
        self.hover_index = -1
        self.pressed_index = -1
        # pointer_x is kept until the envelope reaches zero, so the row
        # shrinks back in place rather than snapping.
        self._begin_zoom()
        self.dock.on_launcher_hover(None)

    def on_press(self, button, x, y, event):
        index = self.index_at(x, y)
        if index < 0:
            return False
        if button == 1:
            self.pressed_index = index
            self._drag_index = index
            self._drag_offset = x - self.icons[index].x
            self._drag_x = x
            self.dock.queue_draw_center()
        return True

    def on_release(self, button, x, y, event):
        index = self.index_at(x, y)
        pressed, self.pressed_index = self.pressed_index, -1
        self._drag_index = -1
        self.dock.queue_draw_center()
        if index < 0:
            return False
        icon = self.icons[index]

        if button == 1:
            if pressed != index:
                return True       # dragged off the icon: no activation
            self._primary(icon, event)
            return True
        if button == 2:
            icon.launch()
            return True
        if button == 3:
            self.show_context_menu(icon, event)
            return True
        return False

    def _primary(self, icon, event):
        self.dock.close_window_list()
        if not icon.running:
            icon.launch()
            return
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            icon.launch()          # Shift+click always opens a new instance
            return
        windows = self.model.visible_windows(icon.key)
        if len(windows) > 1 and self.cfg["click_action"] == "expose":
            self.dock.show_window_list(icon, sticky=True)
            return
        self.model.cycle(icon.key)

    def on_scroll(self, direction, x, y, event):
        index = self.index_at(x, y)
        if index < 0:
            return False
        icon = self.icons[index]
        if icon.running:
            self.model.cycle(icon.key)
            return True
        return False

    def on_drag(self, x):
        """Reorder pinned icons by dragging one past its neighbours."""
        if self._drag_index < 0:
            return False
        if abs(x - self._drag_x) < 6:
            return False
        icon = self.icons[self._drag_index]
        if not icon.pinned:
            return False
        target = self.index_at(x, 0)
        if target < 0 or target == self._drag_index:
            return False
        if not self.icons[target].pinned:
            return False
        self.icons.insert(target, self.icons.pop(self._drag_index))
        self._drag_index = target
        self.pressed_index = target
        self._drag_x = x
        self.save_pinned()
        self.dock.queue_draw_center()
        return True

    # -- context menu ------------------------------------------------------
    def show_context_menu(self, icon, event):
        menu = Gtk.Menu()

        if icon.appinfo is not None:
            actions = icon.appinfo.list_actions()
            labels = set()
            for action in actions:
                name = icon.appinfo.get_action_name(action) or action
                labels.add(_normalise(name))
                item = Gtk.MenuItem(label=name)
                item.connect("activate", lambda _i, a=action: icon.launch(action=a))
                menu.append(item)
            if actions:
                menu.append(Gtk.SeparatorMenuItem())

            # Many apps already ship a "New Window" desktop action; adding our
            # own unconditionally would list it twice.
            if _normalise("New Window") not in labels:
                new_window = Gtk.MenuItem(label="New Window")
                new_window.connect("activate", lambda _i: icon.launch())
                menu.append(new_window)

        if icon.running:
            windows = self.model.windows_for(icon.key)
            if len(windows) > 1:
                item = Gtk.MenuItem(label=f"Show All ({len(windows)})")
                item.connect("activate",
                             lambda _i: [self.model.activate(w) for w in windows])
                menu.append(item)
            minimise = Gtk.MenuItem(label="Minimise All")
            minimise.connect("activate", lambda _i: self.model.minimize_all(icon.key))
            menu.append(minimise)

        menu.append(Gtk.SeparatorMenuItem())
        pin = Gtk.CheckMenuItem(label="Keep in Dock")
        pin.set_active(icon.pinned)
        pin.connect("activate", lambda _i: self.toggle_pin(icon))
        menu.append(pin)

        if icon.running:
            close = Gtk.MenuItem(label="Close All Windows")
            close.connect("activate", lambda _i: self.model.close_all(icon.key))
            menu.append(close)

        menu.show_all()
        menu.attach_to_widget(self.dock.window, None)
        self.dock.keep_menu(menu)
        menu.popup_at_pointer(event)
