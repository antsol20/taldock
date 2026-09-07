"""CPU + memory gauges, drawn as two vertical capsules."""
from __future__ import annotations

import os

from gi.repository import GLib, Gtk

from ..popup import Popup, label, separator
from ..util import rgba, rounded_rect, with_alpha
from .base import PanelItem

GAUGE_W = 7.0
GAUGE_GAP = 7.0
GAUGE_H = 19.0
LABEL_SIZE = 6.5
HISTORY = 60


def _read_cpu():
    """Aggregate jiffies from /proc/stat: (busy, total)."""
    with open("/proc/stat", "r", encoding="ascii") as fh:
        parts = fh.readline().split()
    vals = [int(v) for v in parts[1:11]]
    idle = vals[3] + vals[4]          # idle + iowait
    return sum(vals) - idle, sum(vals)


def _read_mem():
    """(used_bytes, total_bytes, swap_used, swap_total)."""
    info = {}
    with open("/proc/meminfo", "r", encoding="ascii") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) * 1024
    total = info.get("MemTotal", 1)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    swap_total = info.get("SwapTotal", 0)
    swap_used = swap_total - info.get("SwapFree", 0)
    return total - avail, total, swap_used, swap_total


def human(n):
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit not in "BK" else f"{n:.0f}{unit}"
        n /= 1024.0
    return f"{n:.1f}T"


class SysMonItem(PanelItem):
    interval_ms = 2000

    def setup(self):
        self.interval_ms = int(self.cfg.get("sample_interval_ms", 2000))
        self.cpu = 0.0
        self.mem = 0.0
        self.mem_used = self.mem_total = 0
        self.swap_used = self.swap_total = 0
        self.history = []
        self._prev = _read_cpu()
        self._drawn = None      # last (cpu_px, mem_px) actually rendered

    def measure(self, height):
        return GAUGE_W * 2 + GAUGE_GAP + 18

    def poll(self):
        try:
            busy, total = _read_cpu()
            dt = total - self._prev[1]
            self.cpu = max(0.0, min(1.0, (busy - self._prev[0]) / dt)) if dt > 0 else 0.0
            self._prev = (busy, total)
            self.mem_used, self.mem_total, self.swap_used, self.swap_total = _read_mem()
            self.mem = self.mem_used / self.mem_total if self.mem_total else 0.0
        except (OSError, ValueError, ZeroDivisionError):
            return
        self.history.append(self.cpu)
        del self.history[:-HISTORY]
        # The gauges are only GAUGE_H px tall, so most samples do not change
        # a single pixel. Skipping those redraws is most of this widget's
        # idle cost.
        state = (round(GAUGE_H * self.cpu), round(GAUGE_H * self.mem))
        if state != self._drawn:
            self._drawn = state
            self.redraw()

    # -- painting ----------------------------------------------------------
    def _gauge(self, cr, x, y, frac, color):
        rgba(cr, with_alpha(self.theme["fg"], 0.13))
        rounded_rect(cr, x, y, GAUGE_W, GAUGE_H, GAUGE_W / 2)
        cr.fill()
        fill_h = max(GAUGE_W, GAUGE_H * max(0.0, min(1.0, frac)))
        # A high load gets a soft halo so it catches the eye without a label.
        if frac > 0.8:
            rgba(cr, with_alpha(color, (frac - 0.8) * 2.0))
            rounded_rect(cr, x - 2, y + GAUGE_H - fill_h - 2,
                         GAUGE_W + 4, fill_h + 4, (GAUGE_W + 4) / 2)
            cr.fill()
        rgba(cr, color)
        rounded_rect(cr, x, y + GAUGE_H - fill_h, GAUGE_W, fill_h, GAUGE_W / 2)
        cr.fill()

    def _label(self, cr, cx, y, text):
        layout = self.dock.pango_layout(text, LABEL_SIZE, bold=True)
        size = layout.get_pixel_size()
        rgba(cr, self.theme["fg_faint"])
        cr.move_to(cx - size.width / 2, y)
        self.dock.show_layout(cr, layout)
        return size.height

    def draw(self, cr, w, h):
        self.draw_plate(cr, w, h)
        # Gauges alone are ambiguous, so each carries a one-letter caption.
        caption_h = self.dock.pango_layout("C", LABEL_SIZE, bold=True) \
            .get_pixel_size().height
        block_h = GAUGE_H + 1 + caption_h
        x = (w - (GAUGE_W * 2 + GAUGE_GAP)) / 2
        y = (h - block_h) / 2
        self._gauge(cr, x, y, self.cpu, self.theme.load_ramp(self.cpu))
        self._gauge(cr, x + GAUGE_W + GAUGE_GAP, y, self.mem,
                    self.theme.load_ramp(self.mem))
        self._label(cr, x + GAUGE_W / 2, y + GAUGE_H + 1, "C")
        self._label(cr, x + GAUGE_W * 1.5 + GAUGE_GAP, y + GAUGE_H + 1, "M")

    def tooltip(self):
        return (f"CPU {self.cpu*100:.0f}%   "
                f"RAM {human(self.mem_used)}/{human(self.mem_total)}")

    # -- popup -------------------------------------------------------------
    def on_click(self, button, x, y, event):
        if button != 1:
            return False
        if self.popup:
            self.close_popup()
            return True
        self.open_popup(self._build_popup())
        return True

    def _meter_row(self, title, value, frac, color):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        head = Gtk.Box(spacing=8)
        head.pack_start(label(title, "td-title"), False, False, 0)
        head.pack_end(label(value, "td-dim", xalign=1.0), False, False, 0)
        box.pack_start(head, False, False, 0)

        bar = Gtk.DrawingArea()
        bar.set_size_request(-1, 6)

        def draw(_w, cr):
            aw = _w.get_allocated_width()
            rgba(cr, with_alpha(self.theme["fg"], 0.12))
            rounded_rect(cr, 0, 0, aw, 6, 3)
            cr.fill()
            rgba(cr, color)
            rounded_rect(cr, 0, 0, max(6, aw * frac), 6, 3)
            cr.fill()
            return False

        bar.connect("draw", draw)
        box.pack_start(bar, False, False, 0)
        return box

    def _top_processes(self, limit=5):
        """Heaviest processes by RSS. Single-pass over /proc, no sampling."""
        out = []
        page = os.sysconf("SC_PAGE_SIZE")
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/statm", "r", encoding="ascii") as fh:
                    rss = int(fh.read().split()[1]) * page
                with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as fh:
                    name = fh.read().strip()
            except (OSError, ValueError, IndexError):
                continue
            out.append((rss, name))
        out.sort(reverse=True)
        return out[:limit]

    def _build_popup(self):
        pop = Popup(self.dock, padding=13)
        pop.content.set_size_request(258, -1)
        pop.content.get_style_context().add_class("td-popup")

        pop.content.pack_start(
            self._meter_row("Processor", f"{self.cpu*100:.0f}%", self.cpu,
                            self.theme.load_ramp(self.cpu)), False, False, 0)
        pop.content.pack_start(
            self._meter_row("Memory",
                            f"{human(self.mem_used)} / {human(self.mem_total)}",
                            self.mem, self.theme.load_ramp(self.mem)),
            False, False, 0)
        if self.swap_total:
            frac = self.swap_used / self.swap_total
            pop.content.pack_start(
                self._meter_row("Swap",
                                f"{human(self.swap_used)} / {human(self.swap_total)}",
                                frac, self.theme.load_ramp(frac)), False, False, 0)

        pop.content.pack_start(separator(), False, False, 4)
        try:
            load = os.getloadavg()
            pop.content.pack_start(
                label(f"Load  {load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}"
                      f"   ·   {os.cpu_count()} cores", "td-dim"),
                False, False, 0)
        except OSError:
            pass

        pop.content.pack_start(label("Top by memory", "td-title"), False, False, 4)
        for rss, name in self._top_processes():
            row = Gtk.Box(spacing=8)
            row.get_style_context().add_class("td-row")
            row.pack_start(label(name), True, True, 0)
            row.pack_end(label(human(rss), "td-dim", xalign=1.0), False, False, 0)
            pop.content.pack_start(row, False, False, 0)

        btn = Gtk.Button(label="Open System Monitor")
        btn.get_style_context().add_class("td-btn")
        btn.connect("clicked", self._launch_taskmanager, pop)
        pop.content.pack_start(btn, False, False, 5)

        pop.open_at(self.dock.item_center_root(self))
        return pop

    def _launch_taskmanager(self, _btn, pop):
        pop.dismiss()
        for cmd in ("xfce4-taskmanager", "gnome-system-monitor", "htop"):
            try:
                GLib.spawn_async(["/usr/bin/env", cmd],
                                 flags=GLib.SpawnFlags.SEARCH_PATH)
                return
            except GLib.Error:
                continue
