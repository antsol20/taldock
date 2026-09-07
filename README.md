# taldock

A lightweight dock/panel for Xfce on X11. It replaces `xfce4-panel` with a
single full-width bar: **Applications menu** on the left, **Plank-style
launchers** in the middle, **status area and system tray** on the right.

```
┌──────────────────────────────────────────────────────────────────────┐
│ ☰        🗂  🌐  ▶_  ⬢          C M │ 📶  🔋86% 🔊 │ ▪ │  07:47      │
│                    ▁▁                                     Mon 07 Sep │
└──────────────────────────────────────────────────────────────────────┘
   menu       launchers +          cpu/mem  net battery   tray   clock
              running indicators   volume
```

## Why it exists

`xfce4-panel` is solid but dated; Plank is pretty but is only a dock. This is
both: one process, drawn entirely with Cairo, with the magnification and
window indicators of a dock and the tray, clock and system status of a panel.

## Design

- **One window, one drawing area.** The whole bar — background, menu button,
  launchers, every status widget — is painted by a single `GtkDrawingArea`.
  No widget tree, no per-plugin process.
- **Nothing polls that can be pushed.** Volume comes from libpulse's
  subscription API, network from NetworkManager signals, battery from UPower
  properties, the tray from D-Bus. Only CPU/memory is sampled, and it skips
  the repaint when no pixel would change.
- **The bar background is rendered once** into a cached surface, and widgets
  invalidate only their own cell.

Idle cost on the machine it was built for: **~0.1% of one core, ~70 MB RSS**.

## Install

```sh
./install.sh
taldock --replace     # stops xfce4-panel and takes over
```

It installs to `~/.local`, adds an autostart entry, and needs only two
runtime packages (`python3-gi-cairo`, `gir1.2-wnck-3.0`) which the script
installs for you. Everything else — GTK 3, Cairo, libwnck, libpulse — is
already on a stock Xubuntu system.

To go back to the old panel:

```sh
pkill -f taldock; xfce4-panel &
rm -f ~/.config/autostart/taldock.desktop
```

## Using it

| Action | Result |
| --- | --- |
| Click launcher | Launch, or focus / cycle its windows |
| Shift-click, middle-click | Always open a new window |
| Hover a running launcher | Window list (with browser tabs, if available) |
| Right-click launcher | Desktop actions, pin/unpin, close all |
| Drag launcher | Reorder pinned icons |
| Scroll launcher | Cycle that app's windows |
| Scroll volume icon | Adjust volume (hold Shift for fine steps) |
| Middle-click volume | Mute |
| Click menu, then type | Search applications; ↑/↓ and Enter to launch |
| **Super** | Open the applications menu, ready to type |

### The Super key

`install.sh` points XFCE's existing bare-Super binding at `taldock --menu`,
which opens the applications menu with the search box already focused. It
goes through xfconf — the same mechanism Whisker Menu uses — rather than
grabbing the key directly, because an X grab on Super would swallow every
`Super`+key shortcut you have. The previous binding is saved, so
`taldock --unbind-super` puts it back.

`taldock --menu` talks to the running dock over a unix socket and
deliberately avoids importing GTK, so a keypress costs about 90 ms end to
end rather than the ~300 ms a full interpreter start would.

## Browser tab stacking

Browser tabs are not X11 windows, so the dock cannot see them and an
extension cannot see X11 window ids. The bridge closes that gap: a browser
extension pushes its tab list over native messaging to a unix socket, and the
dock matches each browser window to an X window by comparing the active tab's
title to the X window title. Tabs then appear stacked under the browser icon,
and clicking one focuses that window and selects that tab.

`install.sh` registers the native-messaging host. To load the extension:

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → `extension/chrome`

The extension id is pinned to `ahiffodbahdhednjpdndbnalcmpejfhe` by the `key` in its manifest, so the
host manifest keeps working across reloads. Works with Chrome, Chromium,
Brave and Edge. Without it the dock still lists windows, just not tabs.

## Configuration

`~/.config/taldock/config.json` — only values differing from the defaults are
written, so new defaults are picked up on upgrade. Useful keys:

| Key | Default | Meaning |
| --- | --- | --- |
| `position` | `"bottom"` | `bottom` or `top` |
| `icon_size` | `38` | Launcher icon size |
| `zoom`, `zoom_factor` | `true`, `1.32` | Plank-style magnification |
| `autohide` | `"none"` | `none` or `autohide` |
| `opacity` | `0.95` | Bar translucency |
| `launchers` | see config.py | Pinned `.desktop` ids |
| `widgets` | see config.py | Status area order; `sep` draws a divider |
| `monitor` | `"primary"` | Or a connector name such as `eDP-1` |
| `theme` | `{}` | Per-colour overrides, see `taldock/theme.py` |

Run `taldock --config /path/to.json` to try settings without touching your own.

## Requirements

X11 with a compositor (Xfce's is fine), Python 3.9+, GTK 3. Wayland is not
supported: the dock relies on X11 struts and EWMH window management.
