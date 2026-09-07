"""User configuration: JSON at ~/.config/taldock/config.json."""
from __future__ import annotations

import json
import os

from gi.repository import GLib

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "taldock")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Sensible starting dock for a fresh Xubuntu install. Entries that do not
# resolve to an installed .desktop file are skipped silently at load.
DEFAULT_LAUNCHERS = [
    "thunar.desktop",
    "google-chrome.desktop",
    "firefox.desktop",
    "firefox_firefox.desktop",
    "code.desktop",
    "org.gnome.Terminal.desktop",
    "xfce4-terminal.desktop",
]

DEFAULTS = {
    # -- geometry ----------------------------------------------------------
    "position": "bottom",          # bottom | top
    "icon_size": 38,               # launcher icon edge, logical px
    "padding": 7,                  # bar inner padding around icons
    "margin": 8,                   # gap between bar and screen edge
    "side_margin": 10,             # gap between bar and left/right edges
    "radius": 17,                  # bar corner radius
    "monitor": "primary",          # "primary" or a connector name (eDP-1)
    "reserve_space": True,         # set _NET_WM_STRUT_PARTIAL

    # -- behaviour ---------------------------------------------------------
    "zoom": True,                  # plank-style magnification on hover
    "zoom_factor": 1.32,
    "zoom_range": 2.2,             # neighbours affected, in icon widths
    "autohide": "none",            # none | autohide | intellihide
    "click_action": "cycle",       # cycle | expose (cycle windows on click)
    "hover_previews": True,        # window list popup on hover
    "hover_delay_ms": 420,
    "show_unpinned": True,         # running apps that are not pinned
    "group_windows": True,

    # -- content -----------------------------------------------------------
    "launchers": DEFAULT_LAUNCHERS,
    "menu_icon": "xfce4-whiskermenu",
    "menu_label": "Applications",
    "show_menu_label": False,
    "widgets": ["sysmon", "sep", "network", "battery", "audio",
                "sep", "tray", "sep", "clock"],
    "clock_format": "%H:%M",
    "clock_date_format": "%a %d %b",
    "show_clock_date": True,
    "sample_interval_ms": 2000,    # cpu/mem poll period

    # -- appearance --------------------------------------------------------
    "theme": {},                   # colour overrides, see theme.py
    "opacity": 0.95,          # near-opaque: content behind must not read through
    "browser_tabs": True,          # accept tab lists from the browser helper
}


def _merge(base, over):
    """Recursive dict merge; `over` wins. Lists are replaced wholesale."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """Config dict that knows how to persist itself."""

    def __init__(self, path=CONFIG_PATH):
        self.path = path
        super().__init__(_merge(DEFAULTS, self._read()))

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            print(f"taldock: ignoring bad config {self.path}: {exc}")
            return {}

    def save(self):
        """Write only the keys that differ from the defaults, so upgrades
        keep picking up new defaults instead of freezing old ones."""
        diff = {k: v for k, v in self.items() if DEFAULTS.get(k) != v}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(diff, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"taldock: could not save config: {exc}")
