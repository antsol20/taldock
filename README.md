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
  mute; a green dot on the icon whenever the microphone is open
- System tray (StatusNotifier/Ayatana), with full D-Bus menu support
- Clock with a calendar popup

**Dictation — talflow (status area)**
- Hold **Ctrl+Alt+Space**, speak, let go: the words are typed into
  whatever had focus
- The icon is the whole readout — idle, recording, transcribing, typed,
  parked on the clipboard, failed
- Speech-to-text over any OpenAI-compatible endpoint; OpenRouter and Nvidia
  Parakeet by default, and no LLM clean-up pass
- The transcript is **typed**, not pasted, so it lands in terminals and TUIs
  that would ignore a synthesised Ctrl+V
- Nothing is typed into a window that took focus while you were speaking —
  that transcript goes to the clipboard instead

## Requirements

- X11 (**Wayland is not supported** — the dock relies on X11 struts and EWMH)
- A running compositor, such as Xfce's own
- Python 3.9+, GTK 3

On a stock Xubuntu system everything is already present except two small
packages, which the installer adds for you:

```
python3-gi-cairo  gir1.2-wnck-3.0
```

Dictation additionally needs `pw-record` (from `pipewire-bin`, present on a
stock Xubuntu 26.04) and an API key for a transcription service.

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
| **Ctrl+Alt+Space** (hold) | Dictate: records while held, types the transcript on release |
| Click the microphone | Last transcript, model, input device, shortcut |

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

### Dictation

Hold **Ctrl+Alt+Space**, say something, let go. The recording stops on
release, goes to a speech-to-text endpoint, and the text is typed into the
window that was focused when you started. A press shorter than a quarter of
a second is treated as a fumble and thrown away rather than sent.

The icon says where it has got to:

| Icon | State |
| --- | --- |
| Grey microphone | Idle |
| Red, pulsing | Recording |
| Purple, with a turning arc | Transcribing |
| Green, tick | Typed into the target window |
| Amber, card | Focus had moved — transcript is on the clipboard instead |
| Red, cross | Failed; the popup and tooltip say why |
| Amber, struck through | Your microphone is muted — nothing would be recorded |

Put your API key in `~/.config/taldock/talflow.json`, which is created on
first run with mode 0600:

```json
{
  "shortcut": "<Primary><Alt>space",
  "provider": "openrouter",
  "model": "microsoft/mai-transcribe-2",
  "language": "en",
  "api_key": "sk-or-..."
}
```

Then **Reload settings** in the microphone's popup, rather than restarting
the dock. While `api_key` is empty the `TALFLOW_OPENROUTER_KEY` environment
variable is used instead, which is convenient for a first run but means the
key is readable by every process you start.

Two deliberate choices worth knowing:

**The transcript is typed as keystrokes, not pasted.** Alacritty binds paste
to Ctrl+Shift+V and leaves Ctrl+V unbound, so a synthesised Ctrl+V reaches
the shell as the raw byte `0x16` — readline's `quoted-insert` — and pastes
nothing at all; xfce4-terminal behaves the same way. Typed characters need no
such agreement, and your clipboard is left alone. Characters your keyboard
layout cannot produce (an em dash, curly quotes) are typed by borrowing an
unused keycode for the duration.

**Nothing is typed if focus moved.** Synthetic keystrokes go wherever focus
is *now*, so if the window you were dictating into has lost focus by the time
the text comes back, it is put on the clipboard and the icon turns amber
rather than being typed into whatever took its place.

**A muted microphone is visible before you press.** The icon is struck
through in amber whenever the input is muted, and holding the shortcut then
refuses outright rather than recording silence and paying for a request that
comes back as "nothing heard". It clears itself the moment you unmute.

**Recording starts before you finish pressing the shortcut.** PipeWire needs
around 130ms to have a capture stream actually delivering audio, which is
enough to lose the start of your first word. taldock therefore begins
recording when the shortcut's *modifiers* go down, a fraction of a second
before its key, and keeps that audio — so the front of the first word is
already captured by the time you have finished pressing. Set `prewarm` to
`false` to turn this off; the microphone then opens only on the full chord,
at the cost of the clipped start.

The trade-off is that holding those modifiers for some *other* shortcut opens
the microphone briefly too. It is abandoned after 1.5s if the shortcut's own
key never arrives, and the audio is discarded, but the live-microphone dot on
the volume icon will blink in the meantime.

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
| `widgets` | see `config.py` | Status order; `sep` draws a divider, `talflow` is dictation |
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

### Dictation settings

`~/.config/taldock/talflow.json`, written 0600 on first run. It is a separate
file from `config.json` because it holds an API key: `config.json` is the one
you would paste into a bug report, and the dock rewrites it whenever you pin
a launcher.

| Key | Default | Meaning |
| --- | --- | --- |
| `shortcut` | `"<Primary><Alt>space"` | Hold-to-talk combination, in Gtk accelerator syntax |
| `provider` | `"openrouter"` | `openrouter` (any OpenAI-compatible endpoint) or `elevenlabs` |
| `extra` | `{}` | Extra form fields for the provider, e.g. `{"no_verbatim": true}` |
| `endpoint` | `""` | Overrides the provider's own URL |
| `model` | `"microsoft/mai-transcribe-2"` | Sent as the `model` field |
| `language` | `"en"` | Sent with the request; OpenRouter validates it |
| `api_key` | `""` | Falls back to `$TALFLOW_OPENROUTER_KEY` while empty |
| `min_seconds` | `0.25` | Shorter holds are discarded, unsent |
| `max_seconds` | `120` | Hard stop, so a stuck key cannot record for ever |
| `request_timeout` | `45` | Seconds to wait for the transcription |
| `type_delay_ms` | `4` | Pause between batches of keystrokes |
| `type_batch` | `6` | Keystrokes sent per main-loop tick |
| `prewarm` | `true` | Start capturing on the modifiers, before the shortcut's key |

To use ElevenLabs Scribe instead, set `provider` to `elevenlabs`, `model` to
`scribe_v2`, and put an ElevenLabs key in `api_key`. Its `no_verbatim` option,
which strips filler words and false starts, goes in `extra`:

```json
{ "provider": "elevenlabs", "model": "scribe_v2",
  "extra": { "no_verbatim": true } }
```

The shortcut is grabbed from the X server directly rather than going through
xfconf like the Super key, because an xfconf binding runs a command on key
*press* and can say nothing about release — it cannot express "while held".

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
python3 tools/type_selftest.py              # synthetic typing round trip
python3 tools/talflow_selftest.py           # dictation pipeline, end to end
python3 tools/talflow_states.py out.png     # every dictation icon state
python3 tools/talflow_models.py --record sample.wav \
    --expect-file tools/dictation-sample.txt    # compare transcription models
```

`talflow_models.py` records you reading `tools/dictation-sample.txt`, sends
it to every transcription model OpenRouter offers, and reports word error
rate, character error rate (which, unlike WER, counts punctuation and
capitalisation — half of what makes a dictated transcript usable), latency
and what each model actually charged. Worth re-running when picking a model:
the right choice depends on your voice, your microphone and your vocabulary,
not on a leaderboard. A run costs a few pence.

Also test near-silence, by recording a couple of seconds of nothing: a
fumbled press captures room tone, and models differ wildly in what they
invent from it. On 4.9s of silence, four of the fifteen produced text —
Mandarin, an invented paragraph about films, "I think it's a good idea." and
"no," four hundred times over. Anything that does that will type it into
whatever you had focused, so score it before you score accuracy.

The default model was picked this way rather than off the model list, on a
77-second dictation on the author's voice and microphone. Judge it on yours:
on clean studio audio every model scores identically, so the differences
only appear on real speech.

`type_selftest.py` and `talflow_selftest.py` create the window they type
into, so they cannot leak synthetic keystrokes into whatever you are doing.
`talflow_selftest.py` needs the dock's own talflow widget to be stopped: only
one X client can hold a given passive key grab.

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

**Dictation does nothing, or only sometimes.** If the shortcut contains
**Super**, that is almost certainly why. XFCE implements a bare-modifier
shortcut — `/commands/custom/Super_L`, which is how the applications menu
opens — as a passive X grab on Super with an *empty* modifier mask. Press
Super with nothing else held and that grab activates, and xfsettingsd owns
the keyboard until you let go, so the rest of your chord never reaches
taldock. It is therefore order-dependent, which makes it look intermittent:
pressing Ctrl first means the modifier state is no longer empty, the bare
grab does not match, and the shortcut works perfectly. Measured on the
machine this was found on: 2/2 with Ctrl first, 0/2 with Super first, 4/4
either way once the bare Super binding was removed.

The default shortcut avoids Super entirely for this reason. taldock also
checks at startup and puts a note in the microphone's popup if your chosen
shortcut has this problem. To see what is bound bare:

```sh
xfconf-query -c xfce4-keyboard-shortcuts -l -v | grep -E '/(Super|Alt|Control|Shift)_[LR] '
```

**A volume or mute change made in another application is not shown.** Fixed
— but if you are running an older build, this is the symptom: the bar tracked
everything done through its own mixer and nothing done anywhere else, because
`PA_SUBSCRIPTION_MASK_SERVER` was spelled `0x0100` (the deprecated AUTOLOAD
bit) instead of `0x0080`, which made libpulse reject the whole subscription.
Every setter updates its own cache optimistically, so the dock's own controls
always looked correct, which is why it went unnoticed.

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
- **Dictation does not work while one of taldock's own popups is open.** A
  popup holds a seat grab, which outranks the passive key grab, so the
  shortcut never reaches it.
- The `elevenlabs` provider row is written from Scribe's documented request
  shape but has not been exercised against the live service.

Bug reports and patches welcome.

## License

Copyright 2026 antsol20.

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
