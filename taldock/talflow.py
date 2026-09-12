"""talflow: hold a key, speak, get the words typed into whatever has focus.

A deliberately small take on Wispr Flow. Hold the shortcut and the default
microphone is recorded; let go and the audio is posted to a transcription
endpoint; the text comes back and is typed into the window that was focused
when recording started. There is no waveform overlay -- the panel icon's
colour is the whole indicator -- and no LLM clean-up pass: the speech model's
own output is what gets typed.

Three things here are less obvious than they look:

* **The shortcut cannot be an xfconf binding.** Those run a command on press
  and say nothing about release, so they cannot express "while held".
  `hotkey.py` grabs the combination from the X server instead.
* **The transcript is typed, not pasted.** See `xtype.py` -- a synthesised
  Ctrl+V does not paste in Alacritty or xfce4-terminal.
* **Nothing is typed if focus moved.** Synthetic keystrokes go wherever the
  focus is now, not where they were meant, so a transcript whose window has
  gone is put on the clipboard and the icon says so, rather than being typed
  into whatever stole focus mid-transcription.

The provider is a table entry rather than hard-coded URLs, so moving to
ElevenLabs Scribe means adding a row, not rewriting the request.
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

from gi.repository import Gio, GLib, GObject

CONFIG_NAME = "talflow.json"

# States, in the order they occur. The widget maps these to a glyph.
IDLE = "idle"
RECORDING = "recording"
SENDING = "sending"
PASTED = "pasted"          # transcript typed into the target window
CLIPBOARD = "clipboard"    # focus had moved: parked on the clipboard instead
ERROR = "error"

#: How long the success flash stays before the icon returns to idle.
PASTED_HOLD_MS = 1600
#: Polled while waiting for the user to let go of the shortcut's modifiers.
MODIFIER_POLL_MS = 30
MODIFIER_WAIT_MS = 2500

PROVIDERS = {
    # OpenAI's /audio/transcriptions shape. OpenRouter speaks it, and so does
    # OpenAI itself, so one row covers both.
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/audio/transcriptions",
        "auth_header": "Authorization",
        "auth_value": "Bearer {key}",
        "model_field": "model",
        "language_field": "language",
    },
    # Untested here -- no account -- but the shape is what Scribe documents.
    "elevenlabs": {
        "url": "https://api.elevenlabs.io/v1/speech-to-text",
        "auth_header": "xi-api-key",
        "auth_value": "{key}",
        "model_field": "model_id",
        "language_field": "language_code",
    },
}

DEFAULTS = {
    "shortcut": "<Primary><Super>space",
    "provider": "openrouter",
    "endpoint": "",              # blank: use the provider's own URL
    "model": "nvidia/parakeet-tdt-0.6b-v3",
    "language": "en",
    "api_key": "",
    "min_seconds": 0.25,         # shorter than this is a fumble, not speech
    "max_seconds": 120.0,        # hard stop, so a stuck key cannot record for ever
    "request_timeout": 45.0,
    "type_delay_ms": 4,          # per batch of keystrokes
    "type_batch": 6,             # keystrokes per main-loop tick
}

#: Consulted only when `api_key` is blank, so the key can live in the
#: environment during setup and move into the file later.
KEY_ENV = "TALFLOW_OPENROUTER_KEY"


class Settings(dict):
    """talflow's own config file, kept apart from the dock's.

    Separate from `config.json` because it holds an API key: `config.json` is
    the file you would paste into a bug report about the bar's appearance,
    and `Config.save()` rewrites it every time a launcher is pinned. This one
    is written 0600 and never rewritten by the dock.
    """

    def __init__(self, path=None):
        self.path = path or os.path.join(
            GLib.get_user_config_dir(), "taldock", CONFIG_NAME)
        self.error = None
        super().__init__(DEFAULTS)
        self.update(self._read())

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            self.write_template()
            return {}
        except (OSError, ValueError) as exc:
            self.error = f"ignoring bad {self.path}: {exc}"
            print(f"talflow: {self.error}")
            return {}
        if not isinstance(raw, dict):
            self.error = f"{self.path} is not a JSON object"
            return {}
        return raw

    def write_template(self):
        """Drop a commented-by-example config in place on first run."""
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(DEFAULTS, handle, indent=2)
                handle.write("\n")
            os.chmod(self.path, 0o600)
        except OSError as exc:
            print(f"talflow: could not write {self.path}: {exc}")

    @property
    def api_key(self):
        return (self.get("api_key") or os.environ.get(KEY_ENV) or "").strip()

    @property
    def provider(self):
        return PROVIDERS.get(self.get("provider"), PROVIDERS["openrouter"])

    @property
    def url(self):
        return self.get("endpoint") or self.provider["url"]


def transcribe(audio_path, settings):
    """POST the recording. Returns (text, error); exactly one is None.

    Runs on a worker thread: this is seconds of network, and the dock is
    drawing a bar on the main one.
    """
    provider = settings.provider
    key = settings.api_key
    if not key:
        return None, (f"no API key -- put one in {settings.path} "
                      f"or set ${KEY_ENV}")
    try:
        with open(audio_path, "rb") as handle:
            audio = handle.read()
    except OSError as exc:
        return None, f"cannot read recording: {exc}"

    fields = {provider["model_field"]: settings["model"]}
    if settings.get("language"):
        fields[provider["language_field"]] = settings["language"]

    boundary = f"----talflow{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode())
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode())
    parts.append(audio)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    request = urllib.request.Request(settings.url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header(provider["auth_header"],
                       provider["auth_value"].format(key=key))
    try:
        with urllib.request.urlopen(
                request, timeout=float(settings["request_timeout"])) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        try:
            parsed = json.loads(detail)
            detail = parsed.get("error", {}).get("message") or detail
        except ValueError:
            pass
        return None, f"HTTP {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return None, f"network error: {exc.reason}"
    except (ValueError, OSError) as exc:
        return None, f"bad response: {exc}"
    text = payload.get("text")
    if text is None:
        return None, f"no text in response: {json.dumps(payload)[:200]}"
    return text, None


class Talflow(GObject.Object):
    """The state machine behind the panel icon."""

    __gsignals__ = {"state-changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, typist=None, settings=None):
        super().__init__()
        from .hotkey import HotkeyGrabber
        from .xtype import Typist

        self.settings = settings or Settings()
        self.typist = typist or Typist()
        self.state = IDLE
        self.message = ""
        self.last_text = ""
        self.recording_since = 0.0
        self.progress = 0.0        # 0..1 through max_seconds, for the icon

        self._proc = None
        self._target = 0
        self._started = 0.0
        self._cap_source = 0
        self._hold_source = 0
        self._wait_source = 0
        self._wav = os.path.join(
            GLib.get_user_runtime_dir() or f"/run/user/{os.getuid()}",
            "taldock", "talflow.wav")

        self.hotkey = HotkeyGrabber(self.settings["shortcut"])
        self.hotkey.connect("pressed", lambda *_a: self.start())
        self.hotkey.connect("released", lambda *_a: self.stop())
        if not self.typist.ok:
            self._fail(self.typist.error or "no X connection for typing")
        elif self.hotkey.error:
            self._fail(self.hotkey.error)

    # -- state -------------------------------------------------------------
    def _set_state(self, state, message=""):
        self.state = state
        self.message = message
        self.emit("state-changed")

    def _fail(self, message):
        print(f"talflow: {message}")
        self._set_state(ERROR, message)

    @property
    def ready(self):
        return self.hotkey.ok and self.typist.ok

    def describe_shortcut(self):
        """The shortcut as a person would write it."""
        return (self.settings["shortcut"]
                .replace("<Primary>", "Ctrl+").replace("<Control>", "Ctrl+")
                .replace("<Super>", "Super+").replace("<Shift>", "Shift+")
                .replace("<Alt>", "Alt+").replace("space", "Space"))

    # -- recording ---------------------------------------------------------
    def start(self):
        if self.state in (RECORDING, SENDING):
            return
        self._clear_timers()
        try:
            os.makedirs(os.path.dirname(self._wav), mode=0o700, exist_ok=True)
        except OSError as exc:
            return self._fail(f"cannot create {os.path.dirname(self._wav)}: {exc}")
        # Remember where the words are meant to go before anything else can
        # take focus; nothing is typed later unless this is still active.
        self._target = self.typist.active_window()
        try:
            self._proc = Gio.Subprocess.new(
                ["pw-record", "--rate=16000", "--channels=1", "--format=s16",
                 self._wav],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as exc:
            return self._fail(f"cannot start pw-record: {exc.message}")
        self._started = time.monotonic()
        self.recording_since = self._started
        self.progress = 0.0
        self._set_state(RECORDING)
        self._cap_source = GLib.timeout_add(
            int(float(self.settings["max_seconds"]) * 1000), self._hit_cap)

    def _hit_cap(self):
        self._cap_source = 0
        if self.state == RECORDING:
            self.stop()
        return GLib.SOURCE_REMOVE

    def stop(self):
        if self.state != RECORDING or self._proc is None:
            return
        held = time.monotonic() - self._started
        self._clear_timers()
        proc = self._proc
        self._proc = None
        proc.send_signal(signal.SIGTERM)
        if held < float(self.settings["min_seconds"]):
            # A fumbled tap. Let the process die, but do not spend a request
            # on a quarter second of room tone.
            proc.wait_async(None, lambda p, r: self._reap(p, r))
            self._set_state(IDLE)
            return
        self._set_state(SENDING)
        proc.wait_async(None, self._on_recorder_done)

    @staticmethod
    def _reap(proc, result):
        try:
            proc.wait_finish(result)
        except GLib.Error:
            pass

    def _on_recorder_done(self, proc, result):
        self._reap(proc, result)
        # pw-record patches the WAV header on the way out, so the file is
        # only readable once it has actually exited.
        try:
            with wave.open(self._wav) as handle:
                seconds = handle.getnframes() / float(handle.getframerate() or 1)
        except (OSError, wave.Error) as exc:
            return self._fail(f"no usable recording: {exc}")
        if seconds < float(self.settings["min_seconds"]):
            return self._set_state(IDLE)
        thread = threading.Thread(target=self._transcribe_worker,
                                  args=(self._wav,), daemon=True)
        thread.start()

    def _transcribe_worker(self, path):
        text, error = transcribe(path, self.settings)
        GLib.idle_add(self._on_transcribed, text, error)

    # -- delivery ----------------------------------------------------------
    def _on_transcribed(self, text, error):
        try:
            os.unlink(self._wav)
        except OSError:
            pass
        if error:
            self._fail(error)
            return GLib.SOURCE_REMOVE
        text = (text or "").strip()
        if not text:
            self._set_state(IDLE, "nothing heard")
            return GLib.SOURCE_REMOVE
        self.last_text = text
        active = self.typist.active_window()
        if not self._target or active != self._target:
            self._to_clipboard(text, "focus moved while transcribing")
        else:
            self._wait_for_modifiers(text)
        return GLib.SOURCE_REMOVE

    def _to_clipboard(self, text, why):
        from gi.repository import Gdk, Gtk
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self._set_state(CLIPBOARD, f"{why} -- transcript copied instead")

    def _wait_for_modifiers(self, text, waited=0):
        """Do not type while Ctrl or Super is still down.

        The shortcut's own modifiers are usually released a moment after the
        key itself; typing into a live Ctrl would fire shortcuts in the
        target application rather than entering text.
        """
        if self.typist.held_modifiers() and waited < MODIFIER_WAIT_MS:
            self._wait_source = GLib.timeout_add(
                MODIFIER_POLL_MS, self._wait_for_modifiers, text,
                waited + MODIFIER_POLL_MS)
            return GLib.SOURCE_REMOVE
        self._wait_source = 0
        if self.typist.active_window() != self._target:
            self._to_clipboard(text, "focus moved while transcribing")
            return GLib.SOURCE_REMOVE
        self.typist.type_text(
            text, on_done=self._on_typed,
            delay_ms=int(self.settings["type_delay_ms"]),
            batch=int(self.settings["type_batch"]))
        return GLib.SOURCE_REMOVE

    def _on_typed(self, _typed, dropped):
        note = ""
        if dropped:
            note = f"{len(dropped)} character(s) not in the keyboard layout"
        self._set_state(PASTED, note)
        self._hold_source = GLib.timeout_add(PASTED_HOLD_MS, self._back_to_idle)

    def _back_to_idle(self):
        self._hold_source = 0
        if self.state == PASTED:
            self._set_state(IDLE)
        return GLib.SOURCE_REMOVE

    # -- housekeeping ------------------------------------------------------
    def clear(self):
        """Dismiss a sticky clipboard/error state."""
        if self.state in (CLIPBOARD, ERROR, IDLE):
            self._set_state(IDLE)

    def reload(self):
        """Re-read the config file and re-grab the shortcut."""
        self.settings = Settings(self.settings.path)
        if not self.hotkey.bind(self.settings["shortcut"]):
            self._fail(self.hotkey.error or "could not bind shortcut")
            return False
        self._set_state(IDLE, "settings reloaded")
        return True

    def _clear_timers(self):
        for name in ("_cap_source", "_hold_source", "_wait_source"):
            source = getattr(self, name)
            if source:
                GLib.source_remove(source)
                setattr(self, name, 0)

    def shutdown(self):
        self._clear_timers()
        if self._proc is not None:
            self._proc.send_signal(signal.SIGTERM)
            self._proc = None
        self.hotkey.shutdown()
        self.typist.shutdown()
