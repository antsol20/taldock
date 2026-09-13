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
import logging
import logging.handlers
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
#: A pre-armed recorder is abandoned after this long without the actual key,
#: so holding the modifiers for some *other* shortcut cannot leave the
#: microphone open.
PREARM_MAX_MS = 1500
#: Recognised so the state can clear itself when the microphone comes back.
MUTED_MESSAGE = "microphone is muted"
#: Polled while waiting for the user to let go of the shortcut's modifiers.
MODIFIER_POLL_MS = 30
MODIFIER_WAIT_MS = 2500

#: Statuses worth asking again for. 429 is the one actually seen: OpenRouter
#: has a single upstream (Azure) for mai-transcribe-2, so when that provider
#: is busy there is nothing for OpenRouter to fall back to and the rate limit
#: comes straight through.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
#: Ceiling on how long a Retry-After header can make us wait. Someone is
#: sitting there waiting for their words; past this, the fallback is better.
MAX_RETRY_WAIT_S = 4.0
#: Response headers copied into the log when a request fails.
LOGGED_HEADERS = ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
                  "x-ratelimit-reset", "x-generation-id")

log = logging.getLogger("taldock.talflow")

PROVIDERS = {
    # OpenAI's /audio/transcriptions shape. OpenRouter speaks it, and so does
    # OpenAI itself, so one row covers both.
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/audio/transcriptions",
        "auth_header": "Authorization",
        "auth_value": "Bearer {key}",
        "model_field": "model",
        "language_field": "language",
        # Second in the benchmark, clean on silence, and served by Together
        # rather than Azure -- so it does not share mai-transcribe-2's limits.
        # Also a single upstream, though: both being busy at once is possible.
        "fallback_model": "nvidia/parakeet-tdt-0.6b-v3",
    },
    # Scribe. Verified against the live API. Note it reports no cost in the
    # response, unlike OpenRouter, so `usage` comes back with only its own
    # fields. Provider-specific switches such as `no_verbatim` (drop filler
    # words and false starts, scribe_v2 only) go in the `extra` setting.
    "elevenlabs": {
        "url": "https://api.elevenlabs.io/v1/speech-to-text",
        "auth_header": "xi-api-key",
        "auth_value": "{key}",
        "model_field": "model_id",
        "language_field": "language_code",
    },
}

DEFAULTS = {
    # Deliberately not a Super chord: XFCE binds bare Super_L to the
    # applications menu, and that passive grab swallows anything typed while
    # Super is held unless another modifier went down first. See
    # bare_modifier_conflicts() for the full story.
    "shortcut": "<Primary><Alt>space",
    "provider": "openrouter",
    "endpoint": "",              # blank: use the provider's own URL
    # Chosen by tools/talflow_models.py on a real 77s dictation, not from the
    # model list: it beat parakeet on both word and character error rate, and
    # at realistic dictation length the latency difference is noise. Re-run
    # that tool on your own voice before trusting this.
    "model": "microsoft/mai-transcribe-2",
    "language": "en",
    "api_key": "",
    # Extra form fields passed straight to the provider, for options only it
    # understands -- ElevenLabs' {"no_verbatim": true}, say.
    "extra": {},
    "min_seconds": 0.25,         # shorter than this is a fumble, not speech
    "max_seconds": 120.0,        # hard stop, so a stuck key cannot record for ever
    "request_timeout": 45.0,
    # A busy provider (429, 5xx) is asked again this many times, and then the
    # fallback model is tried. null means the provider's own fallback; "" none.
    "retries": 1,
    "retry_delay": 1.0,
    "fallback_model": None,
    # The last few recordings are kept, named by outcome, so a failure can be
    # replayed. They live in $XDG_RUNTIME_DIR: private, and gone at logout.
    "keep_recordings": 5,
    "type_delay_ms": 4,          # per batch of keystrokes
    "type_batch": 6,             # keystrokes per main-loop tick
    # Start capturing when the shortcut's modifiers go down, before its key
    # does. PipeWire needs ~130ms to have a stream delivering audio, which is
    # otherwise clipped off the front of the first word.
    "prewarm": True,
}

#: Consulted only when `api_key` is blank, so the key can live in the
#: environment during setup and move into the file later.
KEY_ENV = "TALFLOW_OPENROUTER_KEY"

#: Which physical keys carry each modifier, for the bare-shortcut check below.
MODIFIER_KEYSYMS = {
    "<Super>": ("Super_L", "Super_R"),
    "<Hyper>": ("Hyper_L", "Hyper_R"),
    "<Alt>": ("Alt_L", "Alt_R"),
    "<Meta>": ("Meta_L", "Meta_R"),
    "<Primary>": ("Control_L", "Control_R"),
    "<Control>": ("Control_L", "Control_R"),
    "<Shift>": ("Shift_L", "Shift_R"),
}


def log_path():
    state = (GLib.get_user_state_dir() if hasattr(GLib, "get_user_state_dir")
             else os.path.expanduser("~/.local/state"))
    return os.path.join(state, "taldock", "talflow.log")


def setup_logging():
    """Send talflow's log to its own file, and its warnings to stderr too.

    A file of its own because the dock's stdout is not somewhere you can
    read: xfce4-session starts its clients with stdout on /dev/null, which is
    exactly how the body of two 429 responses was lost. stderr does reach
    ~/.xsession-errors, but that is shared with every client in the session,
    so only warnings go there. Idempotent, since `Talflow` can be rebuilt.
    """
    if getattr(log, "_talflow_ready", False):
        return
    log._talflow_ready = True
    log.setLevel(logging.INFO)
    log.propagate = False
    stderr = logging.StreamHandler()
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(logging.Formatter("talflow: %(message)s"))
    log.addHandler(stderr)
    path = log_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    except OSError as exc:
        log.warning("cannot open log %s: %s", path, exc)
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)


def bare_modifier_conflicts(accel):
    """Modifiers in `accel` that XFCE also binds as shortcuts on their own.

    This is the failure that cost an afternoon. XFCE implements a bare-modifier
    shortcut (`/commands/custom/Super_L`, which is how the applications menu
    opens) as a passive grab on that key with an empty modifier mask. Press it
    with nothing else held and the grab activates, and xfsettingsd owns the
    keyboard until the key comes back up -- so every later key in the chord,
    including ours, goes to xfsettingsd and never reaches our own passive grab.

    The tell is that it is *order dependent* and therefore looks intermittent:
    press Ctrl before Super and the mask no longer matches an empty one, the
    bare grab never activates, and Ctrl+Super+Space works perfectly. Press
    Super first and nothing happens at all, with no error anywhere. Measured
    here: 2/2 with Ctrl first, 0/2 with Super first, and 2/2 both ways once the
    bare Super binding was removed.
    """
    import subprocess
    hit = []
    for token, keysyms in MODIFIER_KEYSYMS.items():
        if token not in accel:
            continue
        for keysym in keysyms:
            try:
                done = subprocess.run(
                    ["xfconf-query", "-c", "xfce4-keyboard-shortcuts",
                     "-p", f"/commands/custom/{keysym}"],
                    capture_output=True, text=True, timeout=3)
            except (OSError, subprocess.SubprocessError):
                return []          # no xfconf: nothing to say either way
            if done.returncode == 0 and done.stdout.strip():
                hit.append(keysym)
    return hit


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
            log.warning("%s", self.error)
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
            log.warning("could not write %s: %s", self.path, exc)

    @property
    def api_key(self):
        return (self.get("api_key") or os.environ.get(KEY_ENV) or "").strip()

    @property
    def provider(self):
        return PROVIDERS.get(self.get("provider"), PROVIDERS["openrouter"])

    @property
    def url(self):
        return self.get("endpoint") or self.provider["url"]

    @property
    def fallback_model(self):
        """The model to try when `model` stays busy, or "" for none.

        `null` in the file means "whatever suits this provider", because a
        model id means nothing to any provider but its own.
        """
        value = self.get("fallback_model")
        if value is None:
            value = self.provider.get("fallback_model", "")
        return value or ""


def transcribe(audio_path, settings, usage=None, info=None, model=None):
    """POST the recording, once. Returns (text, error); exactly one is None.

    `usage`, if given, is a dict filled in with whatever the provider
    reported alongside the transcript -- seconds billed and cost. `info` gets
    what a caller needs to decide whether to try again: `status`,
    `retryable`, `retry_after`, the untruncated error `body` and a few
    `headers`. Both are passed in rather than returned so the return shape
    stays two values. `model` overrides `settings["model"]`.

    Deliberately a single attempt: `tools/talflow_models.py` benchmarks
    through this, and a silent retry would pass a rate limit off as latency.
    The retry and fallback policy is `Talflow._transcribe_worker`'s.

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

    if info is None:
        info = {}
    info.update(status=None, retryable=False, retry_after=None, body="",
                headers={})
    fields = {provider["model_field"]: model or settings["model"]}
    if settings.get("language"):
        fields[provider["language_field"]] = settings["language"]
    for name, value in (settings.get("extra") or {}).items():
        # multipart carries text, so JSON booleans have to be spelled out.
        fields[name] = ("true" if value else "false") if isinstance(value, bool) \
            else str(value)

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
        try:
            body = exc.read().decode("utf-8", "replace")
        except OSError:
            body = ""
        info.update(status=exc.code, body=body[:4000],
                    retryable=exc.code in RETRYABLE_STATUS,
                    headers={name: exc.headers[name] for name in LOGGED_HEADERS
                             if exc.headers and exc.headers.get(name)})
        try:
            info["retry_after"] = float(exc.headers.get("retry-after"))
        except (TypeError, ValueError, AttributeError):
            pass
        detail = body[:400]
        try:
            error = json.loads(body).get("error", {})
            detail = error.get("message") or detail
            # OpenRouter wraps upstream failures as "Provider returned error"
            # and names the provider only in the metadata.
            provider_name = (error.get("metadata") or {}).get("provider_name")
            if provider_name and provider_name not in detail:
                detail = f"{detail} ({provider_name})"
        except (ValueError, AttributeError):
            pass
        return None, f"HTTP {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        # A refused or unresolvable connection is worth one more try; a
        # timeout has already cost request_timeout seconds and is not.
        info["retryable"] = not isinstance(exc.reason, TimeoutError)
        return None, f"network error: {exc.reason}"
    except (ValueError, OSError) as exc:
        return None, f"bad response: {exc}"
    if usage is not None and isinstance(payload.get("usage"), dict):
        usage.update(payload["usage"])
    text = payload.get("text")
    if text is None:
        return None, f"no text in response: {json.dumps(payload)[:200]}"
    return text, None


class Talflow(GObject.Object):
    """The state machine behind the panel icon."""

    __gsignals__ = {"state-changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, typist=None, settings=None, pulse=None):
        super().__init__()
        from .hotkey import HotkeyGrabber
        from .xtype import Typist

        setup_logging()
        self.settings = settings or Settings()
        self.typist = typist or Typist()
        self.pulse = pulse
        self.state = IDLE
        self.message = ""
        self.last_text = ""
        self.recording_since = 0.0
        self.progress = 0.0        # 0..1 through max_seconds, for the icon

        self._proc = None
        self._prearmed = False
        self._prearm_source = 0
        self._was_muted = False
        self._target = 0
        self._started = 0.0
        self._cap_source = 0
        self._hold_source = 0
        self._wait_source = 0
        self._note = ""            # said alongside a success, e.g. a fallback
        runtime = os.path.join(
            GLib.get_user_runtime_dir() or f"/run/user/{os.getuid()}", "taldock")
        self._wav = os.path.join(runtime, "talflow.wav")
        self.recordings_dir = os.path.join(runtime, "recordings")

        self.warning = ""
        self.hotkey = HotkeyGrabber(self.settings["shortcut"])
        self.hotkey.connect("pressed", lambda *_a: self.start())
        self.hotkey.connect("released", lambda *_a: self.stop())
        self.hotkey.connect("armed", lambda *_a: self.prearm())
        self.hotkey.connect("disarmed", lambda *_a: self.cancel_prearm())
        self._check_shortcut()
        if self.pulse is not None:
            self._was_muted = self.mic_muted
            self.pulse.connect("changed", self._on_pulse)
        if not self.typist.ok:
            self._fail(self.typist.error or "no X connection for typing")
        elif self.hotkey.error:
            self._fail(self.hotkey.error)

    def _check_shortcut(self):
        """Warn about a shortcut that will work only in one press order."""
        self.warning = ""
        if not self.hotkey.ok:
            return
        clashes = bare_modifier_conflicts(self.settings["shortcut"])
        if clashes:
            name = clashes[0].split("_")[0]
            self.warning = (
                f"{name} is also a shortcut on its own, so this only works if "
                f"you press the other keys first")
            log.warning("%s (%s)", self.warning, self.settings["shortcut"])

    # -- state -------------------------------------------------------------
    def _set_state(self, state, message=""):
        self.state = state
        self.message = message
        self.emit("state-changed")

    def _fail(self, message):
        log.error("%s", message)
        self._set_state(ERROR, message)

    @property
    def ready(self):
        return self.hotkey.ok and self.typist.ok

    @property
    def mic_muted(self):
        """True only when we positively know the input is muted.

        Deliberately not `not pulse.mic_live`: that is also false when there
        is no sound server at all, and refusing to record in that case would
        be wrong -- pw-record talks to PipeWire directly and may work fine.
        """
        return bool(self.pulse and self.pulse.available and self.pulse.mic_mute)

    def _on_pulse(self, *_a):
        muted = self.mic_muted
        if muted == self._was_muted:
            return
        self._was_muted = muted
        if not muted and self.state == ERROR and self.message == MUTED_MESSAGE:
            self._set_state(IDLE)       # they unmuted; stop complaining
        else:
            self.emit("state-changed")  # the icon carries the mute marker

    def describe_shortcut(self):
        """The shortcut as a person would write it."""
        return (self.settings["shortcut"]
                .replace("<Primary>", "Ctrl+").replace("<Control>", "Ctrl+")
                .replace("<Super>", "Super+").replace("<Shift>", "Shift+")
                .replace("<Alt>", "Alt+").replace("space", "Space"))

    # -- recording ---------------------------------------------------------
    def _spawn(self, quiet=False):
        """Start pw-record writing to `self._wav`. True if it is running."""
        try:
            os.makedirs(os.path.dirname(self._wav), mode=0o700, exist_ok=True)
            self._proc = Gio.Subprocess.new(
                ["pw-record", "--rate=16000", "--channels=1", "--format=s16",
                 self._wav],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        except (OSError, GLib.Error) as exc:
            self._proc = None
            message = getattr(exc, "message", str(exc))
            if not quiet:
                self._fail(f"cannot start pw-record: {message}")
            return False
        return True

    def prearm(self):
        """Begin capturing before the shortcut's own key is pressed.

        The audio from here to the key press is kept -- a fraction of a second
        of room tone in front of the first word costs nothing and is what
        stops the word being clipped. If the key never comes, because the
        modifiers were held for some other shortcut entirely, the recorder is
        killed and its file discarded by `PREARM_MAX_MS`.
        """
        if self.state in (RECORDING, SENDING) or self._proc is not None:
            return
        if not self.settings.get("prewarm", True) or self.mic_muted:
            return
        if not self._spawn(quiet=True):
            return
        self._prearmed = True
        self._prearm_source = GLib.timeout_add(PREARM_MAX_MS,
                                               self._prearm_expired)

    def _prearm_expired(self):
        self._prearm_source = 0
        self.cancel_prearm()
        return GLib.SOURCE_REMOVE

    def cancel_prearm(self):
        """Throw away a pre-armed recorder that was never claimed."""
        if not self._prearmed:
            return
        if self._prearm_source:
            GLib.source_remove(self._prearm_source)
            self._prearm_source = 0
        self._prearmed = False
        if self._proc is not None:
            self._proc.send_signal(signal.SIGTERM)
            self._proc.wait_async(None, self._reap)
            self._proc = None
        try:
            os.unlink(self._wav)
        except OSError:
            pass

    def start(self):
        if self.state in (RECORDING, SENDING):
            return
        if self.mic_muted:
            # Recording would be silence, and the request would still be paid
            # for. Say so instead; it clears itself when the mic comes back.
            self.cancel_prearm()
            log.info("refused: %s", MUTED_MESSAGE)
            self._set_state(ERROR, MUTED_MESSAGE)
            return
        # Keep a pre-armed recorder rather than restarting it: the audio it
        # has already captured is exactly the part that would be lost.
        if self._prearm_source:
            GLib.source_remove(self._prearm_source)
            self._prearm_source = 0
        self._prearmed = False
        self._clear_timers()
        if self._proc is None and not self._spawn():
            return
        # Remember where the words are meant to go before anything else can
        # take focus; nothing is typed later unless this is still active.
        self._target = self.typist.active_window()
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
        # Out of the recorder's way under a name of its own, so the next
        # recording can never overwrite a file a request is still reading.
        path = self._claim_recording()
        thread = threading.Thread(target=self._transcribe_worker,
                                  args=(path, seconds, self.settings),
                                  daemon=True)
        thread.start()

    def _claim_recording(self):
        if int(self.settings.get("keep_recordings") or 0) <= 0:
            return self._wav
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.recordings_dir, f"{stamp}.wav")
        try:
            os.makedirs(self.recordings_dir, mode=0o700, exist_ok=True)
            os.replace(self._wav, path)
        except OSError as exc:
            log.warning("cannot keep recording: %s", exc)
            return self._wav
        return path

    def _settle_recording(self, path, outcome):
        """Name a kept recording after how it went, and prune old ones."""
        keep = int(self.settings.get("keep_recordings") or 0)
        if path == self._wav or keep <= 0:
            try:
                os.unlink(path)
            except OSError:
                pass
            return
        stem, ext = os.path.splitext(path)
        try:
            os.replace(path, f"{stem}-{outcome}{ext}")
            names = sorted(name for name in os.listdir(self.recordings_dir)
                           if name.endswith(".wav"))
            for name in names[:-keep]:
                os.unlink(os.path.join(self.recordings_dir, name))
        except OSError as exc:
            log.warning("cannot tidy recordings: %s", exc)

    def _transcribe_worker(self, path, seconds, settings):
        """Transcribe, asking again and then falling back while busy.

        Only a busy answer is retried -- 429 or a 5xx, or a connection that
        never got anywhere. A 401 or 400 would fail identically every time,
        and a timeout has already spent request_timeout seconds. The fallback
        is tried only after an HTTP busy answer: a network failure would
        reach the same host and fail the same way.
        """
        name = os.path.basename(path)
        models = [settings["model"]]
        if settings.fallback_model and settings.fallback_model not in models:
            models.append(settings.fallback_model)
        retries = max(0, int(settings.get("retries") or 0))
        delay = float(settings.get("retry_delay") or 0.0)
        text = error = None
        outcome = "error"
        note = ""
        for model in models:
            info = {}
            for attempt in range(1, retries + 2):
                usage, info = {}, {}
                began = time.monotonic()
                text, error = transcribe(path, settings, usage=usage,
                                         info=info, model=model)
                took = time.monotonic() - began
                if error is None:
                    log.info("ok %s attempt %d: audio %.1fs, took %.2fs, "
                             "billed %ss, cost $%.6f, %d chars [%s]",
                             model, attempt, seconds, took,
                             usage.get("seconds", "?"),
                             float(usage.get("cost") or 0.0),
                             len((text or "").strip()), name)
                    if model != models[0]:
                        note = f"via fallback {model.split('/')[-1]}"
                    outcome = "ok" if (text or "").strip() else "empty"
                    GLib.idle_add(self._on_transcribed, text, None, note,
                                  path, outcome)
                    return
                log.warning("failed %s attempt %d after %.2fs: %s [%s]",
                            model, attempt, took, error, name)
                if info.get("headers"):
                    log.info("  headers: %s", json.dumps(info["headers"]))
                if info.get("body"):
                    log.info("  body: %s", info["body"].replace("\n", " "))
                if not info.get("retryable"):
                    break
                if attempt <= retries:
                    wait = info.get("retry_after")
                    wait = delay if wait is None else wait
                    time.sleep(min(max(wait, 0.0), MAX_RETRY_WAIT_S))
            outcome = f"http{info['status']}" if info.get("status") else "error"
            if not (info.get("retryable") and info.get("status")):
                break
            if model != models[-1]:
                log.info("falling back to %s", models[-1])
        if model != models[0]:
            error = (f"{models[0].split('/')[-1]} busy, and fallback "
                     f"{model.split('/')[-1]} failed: {error}")
        GLib.idle_add(self._on_transcribed, None, error, "", path, outcome)

    # -- delivery ----------------------------------------------------------
    def _on_transcribed(self, text, error, note, path, outcome):
        self._settle_recording(path, outcome)
        if error:
            self._fail(error)
            return GLib.SOURCE_REMOVE
        text = (text or "").strip()
        if not text:
            self._set_state(IDLE, "nothing heard")
            return GLib.SOURCE_REMOVE
        self._note = note
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
        log.info("to clipboard: %s", why)
        self._note = ""
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
        notes = [self._note] if self._note else []
        if dropped:
            notes.append(f"{len(dropped)} character(s) not in the keyboard layout")
            log.warning("could not type %r", "".join(sorted(set(dropped))))
        self._note = ""
        self._set_state(PASTED, "; ".join(notes))
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
        self._check_shortcut()
        log.info("settings reloaded: %s via %s, fallback %s",
                 self.settings["model"], self.settings.get("provider"),
                 self.settings.fallback_model or "none")
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
        self.cancel_prearm()
        if self._proc is not None:
            self._proc.send_signal(signal.SIGTERM)
            self._proc = None
        self.hotkey.shutdown()
        self.typist.shutdown()
