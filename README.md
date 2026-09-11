# taldock

A lightweight dock/panel for **Xfce on X11**. It replaces `xfce4-panel` with a
single bar: applications menu on the left, Plank-style launchers in the
middle, system status and tray on the right.

![taldock](assets/dock.png)

One process, drawn entirely with Cairo. Idle cost on the machine it was built
for is about **0.15% of one core and 70 MB RSS**.

---

## Why

`xfce4-panel` is dependable but dated. Plank is prettier but is only a dock —
no tray, no clock, no system status. taldock is both: the magnification and
window indicators of a dock, with the tray, clock and status widgets of a
panel, in one process.

## Features

**Launchers (centre)**
- Plank-style magnification on hover
- Running indicators that encode window count — a dot for one window, a
  lengthening pill for more — highlighted when the app has focus
- Click to launch or focus; click again to cycle that app's windows
- Hover for a window list, including **browser tabs** when the optional
  browser bridge is installed
- Drag to reorder, right-click for the app's own desktop actions, pin/unpin
- Running-but-unpinned apps appear automatically

**Applications menu (left)**
- Ranked search across names, keywords, descriptions and executables
- Category sidebar, keyboard navigation, session actions
- Opens on the **Super** key with the search box already focused

**Status area (right)**
- CPU and memory gauges, with a detail popup listing the heaviest processes
- Wi-Fi via NetworkManager: signal, SSID, network list, join a new network
  with its password, reconnect to saved ones, forget a network
- Battery via UPower: charge, charging state, time remaining
- Volume: scroll to adjust, click for a mixer with output switching and mic
  mute; a red dot on the icon whenever the microphone is open
- System tray (StatusNotifier/Ayatana), with full D-Bus menu support
- Clock with a calendar popup

## Requirements

- X11 (**Wayland is not supported** — the dock relies on X11 struts and EWMH)
- A running compositor, such as Xfce's own
- Python 3.9+, GTK 3

On a stock Xubuntu system everything is already present except two small
packages, which the installer adds for you:

```
python3-gi-cairo  gir1.2-wnck-3.0
```

Optional, used when present: libpulse (volume), NetworkManager (Wi-Fi),
UPower (battery). Each degrades gracefully — a machine with no battery simply
shows no battery widget.

## Install

```sh
git clone https://github.com/antsol20/taldock.git
cd taldock
./install.sh
taldock --replace      # stop xfce4-panel and take over
```

`install.sh` installs into `~/.local`, adds an autostart entry, registers the
browser tab bridge, and points the Super key at the applications menu and the
volume keys at the dock's mixer. It is
re-runnable and needs `sudo` only if the two packages above are missing.

### Uninstall

```sh
taldock --unbind-super
taldock --unbind-media
pkill -f taldock; xfce4-panel &
rm -f ~/.config/autostart/taldock.desktop ~/.local/bin/taldock
rm -rf ~/.local/share/taldock
```

## Using it

| Action | Result |
| --- | --- |
| **Super** | Applications menu, ready to type (Super again closes it) |
| Click launcher | Launch, or focus / cycle its windows |
| Shift-click, middle-click | Always open a new window |
| Hover a running launcher | Window list, with browser tabs when available |
| Right-click launcher | Desktop actions, pin/unpin, close all |
| Drag launcher | Reorder pinned icons |
| Scroll launcher | Cycle that app's windows |
| Scroll volume icon | Adjust volume (Shift for fine steps) |
| Middle-click volume | Mute |
| **Volume keys** | Adjust volume, mute, mute the microphone |

### In the applications menu

Focus stays in the search box the whole time, so you can type at any point.

| Key | Result |
| --- | --- |
| Type | Search every application |
| **↓ / ↑** | Move through whichever column is active |
| **←** | Step over to the categories |
| **→** | Step back to the applications |
| **Tab** | Toggle between the two |
| **Enter** | Launch, or leave the categories for the applications |
| **Esc**, **Super** | Close |

← and → only change column once the text cursor has run out of query to
move through, so a typed search stays editable.

### The Super key

`install.sh` points Xfce's existing bare-Super binding at `taldock --menu`.
This goes through xfconf — the same mechanism Whisker Menu uses — rather than
grabbing the key directly, because an X grab on `Super_L` holds an active
grab for as long as the key is down and would swallow every `Super`+key
shortcut you have. Your previous binding is saved; `taldock --unbind-super`
restores it.

`taldock --menu` talks to the running dock over a unix socket and
deliberately never imports GTK, so a keypress costs about 90 ms end to end
instead of the ~300 ms a full interpreter start would.

## Browser tab stacking

Browser tabs are not X11 windows, so the dock cannot see them; and extensions
cannot see X11 window ids, so the browser cannot identify its own windows to
the dock. The bridge closes that gap: an extension pushes its tab list over
native messaging to a unix socket, and the dock pairs each browser window
with an X window by comparing the active tab's title to the X window title —
which is what browsers put there. Tabs then stack under the browser icon, and
clicking one focuses that window and selects that tab.

`install.sh` registers the native-messaging host. To load the extension:

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → `extension/chrome`

Works with Chrome, Chromium, Brave and Edge. Without it the dock still lists
windows, just not tabs.

## Configuration

`~/.config/taldock/config.json`. taldock writes back only the values that
differ from the defaults, so new defaults are still picked up on upgrade —
but any key **you** put in the file is kept even when it matches a default,
so you can spell settings out explicitly without them being tidied away.

| Key | Default | Meaning |
| --- | --- | --- |
| `position` | `"bottom"` | `bottom` or `top` |
| `icon_size` | `38` | Launcher icon size |
| `margin` | `8` | Gap between the bar and the screen edge it sits on |
| `side_margin` | `10` | Gap at the left and right ends; `0` is edge to edge |
| `padding` | `7` | Bar inner padding around the icons |
| `radius` | `17` | Bar corner radius |
| `zoom`, `zoom_factor` | `true`, `1.32` | Magnification |
| `autohide` | `"none"` | `none` or `autohide` |
| `opacity` | `0.95` | Bar translucency |
| `reserve_space` | `true` | Set `_NET_WM_STRUT_PARTIAL` |
| `launchers` | see `config.py` | Pinned `.desktop` ids |
| `widgets` | see `config.py` | Status order; `sep` draws a divider |
| `menu_width` | `452` | Applications menu width |
| `menu_height` | `372` | Height of its scrolling application list |
| `menu_sidebar_width` | `126` | Category column; `0` hides it |
| `load_warn_at` | `0.40` | CPU/memory gauges leave green above this |
| `load_crit_at` | `0.70` | …are fully amber here, red at 100% |
| `monitor` | `"primary"` | Or a connector name such as `eDP-1` |
| `theme` | `{}` | Colour overrides, see `taldock/theme.py` |

The bar always spans the monitor, so its width is set by `side_margin`, not
by `margin`: `margin` is the gap to the edge the bar sits **on** (the bottom,
by default), and `side_margin` the gap at the two ends. For a bar that runs
the full width of the screen, set `side_margin` to `0`.

`menu_height` sizes the application list, not the whole card — the search
box and footer add a fixed ~133px. Anything that would run off the top of
the screen is clamped automatically, so an over-large value is safe.

Try settings without touching your own config:

```sh
taldock --config /tmp/try.json
```

## How it is built

- **One window, one drawing area.** The whole bar — background, menu button,
  launchers, every status widget — is painted by a single `GtkDrawingArea`.
  Status widgets are not GTK widgets but `PanelItem` objects the dock paints
  in sequence and hit-tests by x position. No widget tree, no plugin
  processes.
- **Nothing polls that can be pushed.** Volume comes from libpulse's
  subscription API (bound with ctypes), network from NetworkManager signals,
  battery from UPower properties, the tray from D-Bus. Only CPU and memory
  are sampled, and those skip the repaint when no pixel would change.
- **The applications menu is built once**, then shown and hidden. Its rows
  live in a `GtkListBox` with filter and sort functions, so searching and
  switching category only re-filter an already-realised list — the menu
  opens in about a millisecond.
- **Cheap frames.** The bar background is rendered once into a cached
  surface; widgets invalidate only their own cell; magnification animates off
  the frame clock and stops itself when nothing is moving.

`CLAUDE.md` documents the architecture in more detail, along with the
PyGObject pitfalls this code has already hit — several of them are not
obvious and cost real debugging time.

## Development

```sh
python3 -m taldock --config /tmp/try.json   # run from a checkout
python3 tools/check_layout_continuity.py    # magnification regression check
```

There is no unit-test suite: this is a GUI, and most of it has to be looked
at. `CLAUDE.md` describes the approach that works, including why
`xfce4-screenshooter` is the wrong tool for capturing hover states.

## Troubleshooting

**The Wi-Fi icon appears twice.** taldock has its own Wi-Fi widget, so
`nm-applet`'s tray icon is redundant. Mask it per-user if you do not want
both:

```sh
printf '[Desktop Entry]\nType=Application\nName=Network\nExec=nm-applet\nHidden=true\n' \
    > ~/.config/autostart/nm-applet.desktop
```

Joining networks does not depend on nm-applet: taldock puts the passphrase
into the connection profile itself, and NetworkManager stores it, so no
secret agent has to be running. Enterprise (802.1X), WEP and hidden networks
ask for more than a passphrase box can, and still open
`nm-connection-editor`.

**The system tray stays empty.** Something else has claimed the
StatusNotifier name. Check who owns it:

```sh
busctl --user status org.kde.StatusNotifierWatcher
```

If that is not taldock, the usual culprit is
`ayatana-indicator-application`, which autostarts on Xfce and will not yield
the name. It exists to feed `xfce4-indicator-plugin`, so with the panel gone
it can be masked:

```sh
systemctl --user mask ayatana-indicator-application.service
```

**A tray icon does nothing when clicked.** Its application destroyed the
icon without telling anyone — the D-Bus name stays registered, so nothing
signals that the object is gone and the stale icon keeps being drawn.
taldock now checks: a click on a dead item, and a sweep every few minutes,
both drop it from the tray. You can confirm an item is a corpse yourself:

```sh
busctl --user get-property org.kde.StatusNotifierWatcher \
    /StatusNotifierWatcher org.kde.StatusNotifierWatcher \
    RegisteredStatusNotifierItems
gdbus call --session --dest :1.55 --object-path /StatusNotifierItem \
    --method org.kde.StatusNotifierItem.Activate 0 0
```

`Object destroyed` or `Method is no longer available` means the application
is at fault, not the tray. Restart that application to get its icon back.

## Known limitations

- **X11 only.** Wayland would need a layer-shell rewrite.
- **StatusNotifier tray only**, not XEmbed. Most modern tray applications use
  StatusNotifier; older XEmbed-only ones will not appear.
- Multi-monitor is implemented but has only been exercised on a single
  1920×1080 display.
- HiDPI scaling is handled in the icon paths but is likewise untested.

Bug reports and patches welcome.

## License

Copyright 2026 antsol20.

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
