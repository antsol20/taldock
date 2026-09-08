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
- **`GLib.spawn_async(["/usr/bin/env", cmd])` always succeeds**, because
  `/usr/bin/env` exists; the failure happens in the child where no exception
  can reach you. A fallback list built that way silently never gets past its
  first entry, which is why "Open System Monitor" did nothing. Use
  `util.launch_first()`, which resolves each binary with
  `GLib.find_program_in_path` first.
- **Never `grab_focus()` a list row while a search entry is open.** It moves
  focus off the entry, so Backspace and the arrow keys stop reaching it and
  typing feels broken. Selection is a style class plus
  `listbox.select_row()`; focus stays in the entry. Category buttons are
  `set_can_focus(False)` for the same reason.
- **Tray icons need the `-symbolic` variant.** Themed tray art is drawn for
  a light panel: nm-applet's `nm-signal-*` is a 22px raster whose dominant
  colour is pure black, so on our bar only its brightest bar survived and it
  read as "one tall bar, or none". `ICONS.symbolic_surface()` prefers the
  monochrome scalable variant and tints it with the panel foreground.
- **A grabbed popup's dismiss test must use root coordinates.** With a seat
  grab GTK routes clicks on our *own* other windows -- the bar -- to the
  popup's `button-press-event`, but does **not** translate the coordinates:
  `event.x/y` still refer to the window the click landed on. Comparing those
  against the popup's allocation called a click on the bar "inside" whenever
  it happened to fall within the popup's width, so the applications menu
  swallowed clicks on the menu button (and on the left third of the bar)
  instead of closing, and could not be toggled shut. `Popup._on_button`
  subtracts the popup window's origin from `event.x_root/y_root`. Note the
  symptom is position-dependent: clicking the bar *beyond* the popup's width
  always worked, which makes this look like a menu-button bug rather than a
  coordinate bug.

- **Animate off the frame clock** (`add_tick_callback`), not a 16ms timeout,
  and ease `pointer_x` toward the raw pointer: motion events arrive coalesced
  and unevenly, so following them directly stutters.

## Behaviour worth knowing before you change it

- **Status dividers collapse.** `Dock._collapse_separators()` hides a `sep`
  unless a visible widget sits on both sides, because widgets can measure
  zero -- an empty system tray, a machine with no battery -- which would
  otherwise leave two dividers stacked together. Measure first, collapse,
  then place.
- **`Config.save()` keeps hand-written keys.** It writes keys that differ
  from `DEFAULTS` *plus* any key that was present in the file on load
  (`self._explicit`). Without that second half, spelling a setting out at
  its default value would be silently deleted by the next save -- and a save
  fires whenever a launcher is pinned or dragged.
- **The applications menu is sized from config, and measured, not guessed.**
  `menu_width` / `menu_height` / `menu_sidebar_width` drive it;
  `_fit_to_screen()` measures the chrome and clamps the list so the card
  cannot run off the top of the screen. It must run **after** `show_all()`:
  an unshown window reports only a minimum size, so the chrome measures as
  nonsense (this was got wrong first time). The category column is inside
  its own scroller, because 11 buttons are taller than a small list and
  would otherwise set a floor `menu_height` could not go below.
- **`margin` and `side_margin` are different axes.** The bar always spans
  the monitor; `margin` is the gap to the edge it sits on and `side_margin`
  the gap at the two ends, so only the latter changes its width. Asked for
  in that order it is easy to reduce `margin` expecting a wider bar and see
  the bar move down by a few pixels instead -- both are in the README table
  now for that reason.

- **The applications menu has two keyboard columns, without moving focus.**
  Focus must stay in the search entry (see the `grab_focus` trap above), so
  `AppMenuPopup.pane` is our own notion of which column the arrows drive,
  drawn with the `td-idle` / `td-active` style classes rather than GTK
  focus. Left/Right switch columns but only once the caret has run out of
  query text in that direction (`_caret_can_leave`), or a typed search could
  not be edited. `_move_category` sets `_syncing_category` while it toggles
  a radio button, because `_on_category` otherwise cannot tell an arrow key
  from a mouse click -- and a click should hand the arrows back to the apps.

- **GTK themes bold the selected row; we undo it.** Dracula's
  `widgets/cell-row.css` has `row:selected { font: bold; }`, so keyboard
  selection rendered bold while mouse hover -- which has no such rule --
  did not, and the two read as different things. `popup.py` sets
  `font-weight: normal` on selected row labels; the accent background is
  the whole selection cue. Check the theme before assuming our CSS is at
  fault: `grep -rn 'bold' /usr/share/themes/<name>/gtk-3.0`.

- **Dead tray items have to be found by asking.** An app is meant to drop
  its bus name or call `UnregisterStatusNotifierItem` when it destroys its
  tray icon. The Claude desktop app does neither: it destroys the object,
  keeps the connection open, and the bus then answers property reads with
  `UnknownMethod` ("Method is no longer available") and method calls with
  `Failed` ("Object destroyed"). Nothing is signalled, so the last icon we
  fetched sat in the tray for ever and did nothing when clicked.
  `TrayItem.is_alive()` / `probe()` ask the peer; a dead answer emits `gone`
  and the host drops the item. `StatusNotifierHost._reap` is the one piece
  of polling in the dock (`REAP_INTERVAL_S`), and it is async on purpose --
  a synchronous sweep would stall the bar for the call timeout whenever a
  tray application hung. Distinguish carefully: a timeout means busy, not
  dead, and plenty of items (nm-applet) legitimately implement no
  `Activate`.

- **The CPU/memory colour ramp is configurable.** `Theme.load_ramp` is green
  below `load_warn_at`, blends to amber by `load_crit_at`, then to red at
  100%; the gauge glow starts at `load_crit_at` so colour and halo cannot
  drift apart. Defaults are 0.40 and 0.70, verified under real load: 26%
  green, 51% yellow-green, 76% amber, 100% red. They were originally
  0.60/0.85, which made everything below 60% look identical -- a normally
  busy desktop never left green. Widen them again only if the gauges feel
  alarmist.

## Things that look wrong but are deliberate

- **The applications menu is built once and reused**, not rebuilt per open.
  Realising its widget tree costs ~300ms; showing an already-realised one
  costs under a millisecond, and it is on the Super key. `Popup(persistent=
  True)` hides instead of destroying, `Dock.prewarm_app_menu()` pays the cost
  at startup, and rows are filtered with `Gtk.ListBox` filter/sort funcs
  rather than being rebuilt.
- **The Super key goes through xfconf, not an X grab.** `XGrabKey` on
  `Super_L` activates an active grab for as long as the key is held, so every
  `Super`+key combo would stop reaching xfwm4. XFCE already binds a bare
  Super press (that is how Whisker Menu does it); `--bind-super` just
  repoints it and saves the old value.
- **`--menu` must not import gi.** It is on the keypress path; the whole
  point of the control socket is to keep that under ~100 ms.
- **Super-to-close is handled inside the popup, not by the shortcut.** While
  the menu is open it holds a seat grab, so xfwm4 never sees the key and the
  xfconf shortcut cannot fire to toggle it shut. `AppMenuPopup._on_key`
  treats Super/Meta like Escape.
- **`ayatana-indicator-application` competes for the tray.** It autostarts
  (`/etc/xdg/autostart` plus a systemd user unit), claims
  `org.kde.StatusNotifierWatcher`, and does **not** allow name replacement --
  so whichever of it and taldock starts first wins, and tray items register
  with the winner. Symptom: the tray silently stays empty while
  `busctl --user status org.kde.StatusNotifierWatcher` names something else.
  It exists to feed `xfce4-indicator-plugin`, so it is masked on this
  machine. Check the name's owner before assuming the tray code is at fault.
- **Reply to `RegisterStatusNotifierItem` before building the item's proxy.**
  Constructing it queries the caller synchronously, and the caller may still
  be blocked on that very reply.
- **Only one Dock may exist per session.** A second one unlinks and rebinds
  the control and tab sockets, silently breaking the running dock's Super
  key. Both servers probe for a live socket first, and `main()` refuses to
  start a second instance. Test tools that construct a `Dock` directly get
  `secondary = True` and no sockets -- but `secondary` covers *only* the two
  socket servers. A second `Dock` still builds a `StatusNotifierHost`, which
  owns the watcher name with `REPLACE`, so it takes the tray away from the
  running dock. Test tray code against a throwaway item on the bus instead
  of constructing a `Dock`.

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
