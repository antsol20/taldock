# taldock — notes for future sessions

A dock/panel replacing `xfce4-panel` on Xubuntu 26.04 (Xfce 4.20, X11).
Read `README.md` first for what it does and how it is configured; this file
records the things that are not obvious from the code.

## Layout of the code

| File | Role |
| --- | --- |
| `dock.py` | The window: geometry, struts, layout, painting, input dispatch |
| `launchers.py` | Centre zone — icon state, magnification, indicators, menus |
| `windows.py` | libwnck wrapper: window→app matching and grouping |
| `appmenu.py` | Applications menu popup |
| `windowlist.py` | Hover popup listing an app's windows and browser tabs |
| `popup.py` | Shared rounded popup window + the CSS for popup contents |
| `widgets/` | Status-area cells (`PanelItem` subclasses), registered in `dock.WIDGETS` |
| `sni.py`, `dbusmenu.py` | StatusNotifier tray host and its menu renderer |
| `pulse.py`, `nm.py` | ctypes libpulse binding; NetworkManager over GDBus |
| `tabs.py` | Unix-socket server receiving browser tab lists |
| `x11.py` | ctypes libX11 shim for what GDK does not expose |

The whole bar is one `GtkDrawingArea`. Status widgets are **not** GTK
widgets — they are `PanelItem` objects the dock paints in sequence and
hit-tests by x position. Add a widget by subclassing `PanelItem`
(`measure`/`draw`/`on_click`) and registering it in `dock.WIDGETS`.

## Traps already hit — do not reintroduce

- **`Gdk.Rectangle(x=…, y=…)` silently returns zeros.** It is a boxed struct
  and PyGObject ignores constructor arguments. Use `util.rect()`.
- **`Gdk.property_change` and `Gdk.Window.add_filter` are not introspectable**
  in PyGObject. That is why `x11.py` exists (struts go through libX11), and
  why the tray is StatusNotifier-only rather than XEmbed — XEmbed needs raw
  `ClientMessage` handling that GDK will not hand us.
- **Never call our own D-Bus service synchronously.** Once taldock owns
  `org.kde.StatusNotifierWatcher`, a `call_sync` to it blocks the main loop
  that must answer it — a hard deadlock that stopped struts being applied.
  `sni.py` short-circuits when the watcher's owner is our own unique name.
- **Hover popups must not take a seat grab.** The grab makes the dock see a
  leave, which closes the popup, which restores the hover: a flicker loop.
  `Popup(..., grab=False)` for hover, `grab=True` for click-opened popups.
- **A handler returning `True` stops later handlers.** Cost an hour of
  debugging when instrumenting `_on_motion` from outside.
- **Magnification layout must be continuous in `pointer_x`.** The first
  version anchored the row to the icon nearest the pointer; when the nearest
  icon changed, the row jumped ~10px in a single motion event, which is what
  made it feel jerky. `layout()` now expands the row about the pointer's
  fractional position along it. `tools/check_layout_continuity.py` is a
  regression check for exactly this.
- **Animate off the frame clock** (`add_tick_callback`), not a 16ms timeout,
  and ease `pointer_x` toward the raw pointer: motion events arrive coalesced
  and unevenly, so following them directly stutters.

## Two things that look wrong but are deliberate

- **The Super key goes through xfconf, not an X grab.** `XGrabKey` on
  `Super_L` activates an active grab for as long as the key is held, so every
  `Super`+key combo would stop reaching xfwm4. XFCE already binds a bare
  Super press (that is how Whisker Menu does it); `--bind-super` just
  repoints it and saves the old value.
- **`--menu` must not import gi.** It is on the keypress path; the whole
  point of the control socket is to keep that under ~100 ms.

## Verifying changes

There is no test suite; this is a GUI that must be looked at. The workflow
that works, all in the scratchpad:

- `drive.py` — XTest pointer/keyboard driver (`move:x,y`, `click:1`, `key:Escape`).
- `cap.py` — screen capture via `Gdk.pixbuf_get_from_window`. **Use this, not
  `xfce4-screenshooter`**: the screenshooter perturbs the pointer, so hover
  and magnification collapse before the frame is taken.
- The screen blanks while unattended; `xfce4-screensaver-command --deactivate`
  brings it back (it blanks rather than locks on this machine).
- `pkill -f taldock` from a shell **kills the shell too**, because `-f`
  matches the shell's own command line. Loop over `pgrep -x python3` and
  check `/proc/$p/cmdline` instead.

Run a throwaway config with `--config` rather than editing `~/.config/taldock`.

## Known gaps

- **XEmbed tray icons are not supported**, only StatusNotifier/Ayatana. This
  machine's tray is SNI-only (`_NET_SYSTEM_TRAY_S0` was not even owned), so it
  has not mattered. Supporting XEmbed means a second X connection in `x11.py`
  driven by a GLib fd watch, since GDK cannot deliver the ClientMessages.
- **Wayland is unsupported** and would need a full rewrite (layer-shell).
- Multi-monitor is implemented (`monitor` config key, `monitors-changed`
  handling) but has only been exercised on a single 1920×1080 display.
