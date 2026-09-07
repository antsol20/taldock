"""A hairline divider between groups of status items."""
from __future__ import annotations

from ..util import rgba
from .base import PanelItem


class SeparatorItem(PanelItem):
    """Purely decorative; never takes pointer events."""

    interactive = False

    def measure(self, height):
        return 13

    def draw(self, cr, w, h):
        inset = h * 0.28
        rgba(cr, self.theme["sep"])
        cr.set_line_width(1.0)
        cr.move_to(round(w / 2) + 0.5, inset)
        cr.line_to(round(w / 2) + 0.5, h - inset)
        cr.stroke()
