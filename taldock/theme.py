"""Colour palette. Dark, translucent, tuned to sit over any wallpaper."""
from __future__ import annotations

from .util import hex_rgba, with_alpha

# Dracula-adjacent base, deliberately a little cooler and darker than the
# stock theme so light wallpapers still read behind the translucent bar.
PALETTE = {
    "bg":          "#181926",
    "bg_hi":       "#242639",   # top edge gradient stop
    "border":      "#ffffff1c",
    "sheen":       "#ffffff12",  # 1px inner highlight along the top
    "shadow":      "#00000059",

    "fg":          "#e8e8f2",
    "fg_dim":      "#9aa0bd",
    "fg_faint":    "#6b7192",

    "accent":      "#bd93f9",   # running / active indicator
    "accent_alt":  "#8be9fd",
    "ok":          "#50fa7b",
    "warn":        "#ffb86c",
    "crit":        "#ff5555",

    "hover":       "#ffffff16",  # icon hover plate
    "active":      "#ffffff24",  # pressed / open-menu plate
    "popup_bg":    "#1b1c2b",
    "popup_border": "#ffffff20",
    "sep":         "#ffffff14",
}


class Theme:
    """Resolved colours plus a couple of derived metrics."""

    def __init__(self, config):
        raw = dict(PALETTE)
        raw.update(config.get("theme") or {})
        self.c = {k: hex_rgba(v) for k, v in raw.items()}
        self.opacity = float(config.get("opacity", 0.86))
        # The bar background carries the configured opacity; everything
        # painted on top of it stays fully opaque.
        self.c["bg"] = with_alpha(self.c["bg"], self.opacity)
        self.c["bg_hi"] = with_alpha(self.c["bg_hi"], self.opacity)
        self.c["popup_bg"] = with_alpha(self.c["popup_bg"], min(1.0, self.opacity + 0.11))
        self.font = config.get("font", "Noto Sans")

    def __getitem__(self, key):
        return self.c[key]

    def get(self, key, default=None):
        return self.c.get(key, default)

    def load_ramp(self, frac):
        """Green -> orange -> red, for meters. `frac` is 0..1."""
        from .util import mix
        if frac < 0.6:
            return self.c["ok"]
        if frac < 0.85:
            return mix(self.c["ok"], self.c["warn"], (frac - 0.6) / 0.25)
        return mix(self.c["warn"], self.c["crit"], min(1.0, (frac - 0.85) / 0.15))
