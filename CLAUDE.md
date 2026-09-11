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

- **PyGObject does not expose the XF86 keysyms.** There is no
  `Gdk.KEY_XF86AudioRaiseVolume` -- the lookup raises `AttributeError` at the
  moment the key is pressed, inside a signal handler, so it surfaces as a
  traceback in the dock's log rather than as an import error, and the key
  simply appears to do nothing. `popup.py` spells the four values out from
  `X11/XF86keysym.h`.

- **A popup with a seat grab swallows the media keys.** While one is open
  xfsettingsd never sees them, so the xfconf shortcut that runs
  `taldock --volume-up` cannot fire -- the same mechanism that forces
  Super-to-close to be handled inside the applications menu. `Popup._on_key`
  routes them to the dock, which is why the volume keys work while the mixer
  itself is open. Any popup that overrides `_on_key` has to call
  `self._media_key(event)` on its way out, as `AppMenuPopup` does.

- **Do not connect `key-press-event` in a Popup subclass.** `Popup.__init__`
  already connects `self._on_key`, and Python resolves that to the
  subclass's override -- so `AppMenuPopup` connecting it again ran the
  handler twice for every keypress. It was invisible only because the
  handler returns `True` for everything it acts on, which stops the second
  invocation; the keys it ignores ran it twice for nothing. A media key
  reached that way would have stepped the volume twice.

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

- **Joining Wi-Fi needs no secret agent, and a failed join must clean up
  after itself.** nm-applet is NetworkManager's secret agent -- it is what
  used to prompt for passphrases -- and this machine masks it, because
  taldock draws its own Wi-Fi icon. We do not reimplement it. Saved profiles
  here report `psk-flags: 0`, meaning NM stores the passphrase itself, so
  `connect_new()` passes the passphrase straight into
  `AddAndActivateConnection` and NM saves it; that is what
  `nmcli device wifi connect <ssid> password <pw>` does too. The consequence
  to respect: a *wrong* passphrase is saved just as silently. Every later
  attempt would then find that profile through `saved_connection_for()`,
  reuse the bad passphrase and fail identically, with nothing left to ask
  again -- the network becomes permanently unjoinable from the dock. So
  `_ConnectAttempt.finish()` deletes the profile it created on any failure.
  Note also that NM answers `AddAndActivateConnection` when it *accepts* the
  request, not when the network is up: the verdict comes from watching the
  ActiveConnection's `State`, with a timeout, and never from the call
  returning. WPA2/WPA3 transitional APs advertise both PSK and SAE and must
  be joined as `sae`, or the join fails on a WPA3-only AP.

- **A mute displays as zero, in both places, and is not stored anywhere.**
  The mixer's percentage and slider, and the level bar that flashes under
  the tray icon, all show 0 while muted and the previous level again as soon
  as it is lifted. Nothing remembers that level: PulseAudio keeps it behind
  the mute, so the display is just `0.0 if muted else pulse.volume` in both
  `AudioItem.draw()` and the popup's `shown_volume()`. Do not "fix" either
  one back to the raw level -- showing 54% beside a slider that cannot be
  heard is two answers to the same question. The muted speaker also takes
  `warn` rather than `fg_dim`: muting is the answer to "why is there no
  sound", so it should not recede exactly when it is being looked for. An
  unavailable sound server still dims, because that one really is disabled.

- **The live-microphone dot is a separate mark, on purpose.** Output mute
  and microphone state are independent -- the speakers can be muted while
  the microphone is open -- so one glyph colour cannot say both. The speaker
  keeps saying *output*, and `_mic_pip()` adds a red dot for the input. It
  is placed clear of the speaker's arcs, which reach `cy±6.2s` at their
  widest, and of the mute cross, which stops at `cy-3.2s`; the ring of bar
  colour behind it is what keeps it legible where the two nearly touch.
  `pulse.mic_live` deliberately checks `mic_index` as well as `mic_mute`,
  because `mic_mute` starts False -- a machine with no input at all would
  otherwise claim a live microphone for ever. And note `_on_source` now
  emits `changed`: it did not, so the bar repainted on a microphone change
  only by luck, when the sink list that follows a refresh happened to emit
  first, and then with the old state still in hand.

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
- **`--menu` and the volume flags must not import gi -- or argparse.** They
  are on a keypress path, and a held volume key repeats, so `main()` answers
  a bare control flag from `CONTROL_FLAGS` before importing anything beyond
  `os`/`sys`. argparse alone cost ~25ms of a ~75ms round trip on this
  machine (it pulls in `re`, `dataclasses` and `_colorize`); deferring it,
  `shutil` and `subprocess` took the whole path to ~25ms.

- **The volume keys are ours now, because nothing else claimed them.**
  `xfce4-pulseaudio-plugin` grabbed `XF86AudioRaiseVolume` and friends, and
  it is a *panel plugin* -- it went with `xfce4-panel`, and neither it nor
  `xfce4-volumed-pulse` is installed, so the keys reached no one at all.
  `--bind-media` points them at `taldock --volume-up` etc. through the same
  xfconf mechanism as the Super key, and `install.sh` does it. Test them for
  real with `tools/drive.py key:XF86AudioRaiseVolume`: the keysyms are in
  the keymap, so this exercises xfsettingsd's dispatch too, not just our
  handler.
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

## Starting up at login

The dock's own startup is ~400ms to first draw (`TALDOCK_TIMING=1` prints
marks to stderr, which xfce4-session sends to `~/.xsession-errors`;
`tools/login-timeline.sh` reads them back). Everything else about login
latency is xfce4-session's sequencing, and it is worth understanding before
touching it:

- **xfce4-session starts its clients in groups of equal `Priority`, and a
  group whose clients never register with the session manager costs ~8
  seconds** before the next group starts. Autostart entries -- where taldock
  used to live -- run only after the last group. On this machine that put
  the dock 17 seconds into the session. `tools/session-slot.sh` moves it
  into the client list instead, where it starts at +1s.
- **The priority it gets matters more than the slot.** GTK3 dropped XSMP, so
  taldock never registers and any group it has to itself adds 8 seconds to
  everything after it -- including xfdesktop, so the desktop icons appear
  late and it looks like the dock broke something. Priority 30 shares
  `Thunar --daemon`'s group, which does not register either, so the wait is
  one the session already paid. Measure with offsets *from xfce4-session's
  own start*, not wall clock: two logins do not begin at the same second.
- **A failed spawn costs nothing.** The `xfce4-panel` entry left behind in
  the failsafe client list after the package was removed logs a warning in
  `~/.xsession-errors` and looks like the culprit, but a group with nothing
  running in it advances immediately.
- The autostart entry stays installed as a fallback. Both fire; the second
  one exits at `is_live()`, which its timing marks show ending at preflight.

## Verifying changes

There is no test suite; this is a GUI that must be looked at. The workflow
that works:

- `tools/drive.py` — XTest pointer/keyboard driver (`move:x,y`, `click:1`,
  `key:Escape`, `type:hello`). Keys are keysym names, so
  `key:XF86AudioRaiseVolume` fires a media key exactly as the hardware does.
- `tools/cap.py` — screen capture via `Gdk.pixbuf_get_from_window`. **Use
  this, not `xfce4-screenshooter`**: the screenshooter perturbs the pointer,
  so hover and magnification collapse before the frame is taken.
- Both used to be rewritten into the scratchpad each session, which is wiped
  between them; they live in `tools/` so they stop being rewritten.
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
