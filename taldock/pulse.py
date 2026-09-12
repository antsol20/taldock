"""Minimal ctypes binding to libpulse, driven by the GLib main loop.

pipewire-pulse speaks the PulseAudio protocol, so this works on both. Only
the leading fields of pa_sink_info are declared -- they have been ABI-stable
for well over a decade and we never allocate one, we only read the server's.
Everything is event-driven: no polling, no subprocesses.
"""
from __future__ import annotations

import ctypes as C

from gi.repository import GLib, GObject

PA_VOLUME_NORM = 0x10000
PA_CHANNELS_MAX = 32
PA_CONTEXT_READY = 4
PA_CONTEXT_FAILED = 5
PA_CONTEXT_TERMINATED = 6
PA_SUBSCRIPTION_MASK_SINK = 0x0001
PA_SUBSCRIPTION_MASK_SOURCE = 0x0002
# 0x0080, not 0x0100: 0x0100 is the deprecated AUTOLOAD bit, and passing it
# made pa_context_subscribe reject the whole mask, so *no* subscription
# events arrived at all -- the bar only ever noticed a volume or mute change
# made elsewhere when something else happened to trigger a refresh.
PA_SUBSCRIPTION_MASK_SERVER = 0x0080
PA_INVALID_INDEX = 0xFFFFFFFF


class PaSampleSpec(C.Structure):
    _fields_ = [("format", C.c_int), ("rate", C.c_uint32), ("channels", C.c_uint8)]


class PaChannelMap(C.Structure):
    _fields_ = [("channels", C.c_uint8), ("map", C.c_int * PA_CHANNELS_MAX)]


class PaCVolume(C.Structure):
    _fields_ = [("channels", C.c_uint8),
                ("values", C.c_uint32 * PA_CHANNELS_MAX)]


class PaSinkInfo(C.Structure):
    """Truncated after `mute`; later fields are never accessed."""
    _fields_ = [
        ("name", C.c_char_p),
        ("index", C.c_uint32),
        ("description", C.c_char_p),
        ("sample_spec", PaSampleSpec),
        ("channel_map", PaChannelMap),
        ("owner_module", C.c_uint32),
        ("volume", PaCVolume),
        ("mute", C.c_int),
    ]


PaSourceInfo = PaSinkInfo   # identical leading layout


class PaServerInfo(C.Structure):
    _fields_ = [
        ("user_name", C.c_char_p),
        ("host_name", C.c_char_p),
        ("server_version", C.c_char_p),
        ("server_name", C.c_char_p),
        ("sample_spec", PaSampleSpec),
        ("default_sink_name", C.c_char_p),
        ("default_source_name", C.c_char_p),
    ]


CTX_STATE_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)
SUBSCRIBE_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_uint32, C.c_void_p)
SINK_CB = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(PaSinkInfo), C.c_int, C.c_void_p)
SOURCE_CB = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(PaSourceInfo), C.c_int, C.c_void_p)
SERVER_CB = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(PaServerInfo), C.c_void_p)
SUCCESS_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_void_p)


def _load():
    pa = C.CDLL("libpulse.so.0")
    glue = C.CDLL("libpulse-mainloop-glib.so.0")
    glue.pa_glib_mainloop_new.restype = C.c_void_p
    glue.pa_glib_mainloop_new.argtypes = [C.c_void_p]
    glue.pa_glib_mainloop_get_api.restype = C.c_void_p
    glue.pa_glib_mainloop_get_api.argtypes = [C.c_void_p]
    glue.pa_glib_mainloop_free.argtypes = [C.c_void_p]

    pa.pa_context_new.restype = C.c_void_p
    pa.pa_context_new.argtypes = [C.c_void_p, C.c_char_p]
    pa.pa_context_connect.restype = C.c_int
    pa.pa_context_connect.argtypes = [C.c_void_p, C.c_char_p, C.c_int, C.c_void_p]
    pa.pa_context_get_state.restype = C.c_int
    pa.pa_context_get_state.argtypes = [C.c_void_p]
    pa.pa_context_set_state_callback.argtypes = [C.c_void_p, CTX_STATE_CB, C.c_void_p]
    pa.pa_context_set_subscribe_callback.argtypes = [C.c_void_p, SUBSCRIBE_CB, C.c_void_p]
    pa.pa_context_subscribe.restype = C.c_void_p
    pa.pa_context_subscribe.argtypes = [C.c_void_p, C.c_int, SUCCESS_CB, C.c_void_p]
    pa.pa_context_get_server_info.restype = C.c_void_p
    pa.pa_context_get_server_info.argtypes = [C.c_void_p, SERVER_CB, C.c_void_p]
    pa.pa_context_get_sink_info_by_name.restype = C.c_void_p
    pa.pa_context_get_sink_info_by_name.argtypes = [C.c_void_p, C.c_char_p, SINK_CB, C.c_void_p]
    pa.pa_context_get_sink_info_list.restype = C.c_void_p
    pa.pa_context_get_sink_info_list.argtypes = [C.c_void_p, SINK_CB, C.c_void_p]
    pa.pa_context_get_source_info_by_name.restype = C.c_void_p
    pa.pa_context_get_source_info_by_name.argtypes = [C.c_void_p, C.c_char_p, SOURCE_CB, C.c_void_p]
    pa.pa_context_set_sink_volume_by_index.restype = C.c_void_p
    pa.pa_context_set_sink_volume_by_index.argtypes = [
        C.c_void_p, C.c_uint32, C.POINTER(PaCVolume), SUCCESS_CB, C.c_void_p]
    pa.pa_context_set_sink_mute_by_index.restype = C.c_void_p
    pa.pa_context_set_sink_mute_by_index.argtypes = [
        C.c_void_p, C.c_uint32, C.c_int, SUCCESS_CB, C.c_void_p]
    pa.pa_context_set_source_mute_by_index.restype = C.c_void_p
    pa.pa_context_set_source_mute_by_index.argtypes = [
        C.c_void_p, C.c_uint32, C.c_int, SUCCESS_CB, C.c_void_p]
    pa.pa_context_set_default_sink.restype = C.c_void_p
    pa.pa_context_set_default_sink.argtypes = [C.c_void_p, C.c_char_p, SUCCESS_CB, C.c_void_p]
    pa.pa_context_disconnect.argtypes = [C.c_void_p]
    pa.pa_context_unref.argtypes = [C.c_void_p]
    pa.pa_operation_unref.argtypes = [C.c_void_p]
    return pa, glue


class Sink:
    __slots__ = ("index", "name", "description", "volume", "mute", "channels")

    def __init__(self, info):
        self.index = int(info.index)
        self.name = (info.name or b"").decode("utf-8", "replace")
        self.description = (info.description or b"").decode("utf-8", "replace")
        self.channels = max(1, int(info.volume.channels))
        peak = max(info.volume.values[i] for i in range(self.channels))
        self.volume = peak / PA_VOLUME_NORM
        self.mute = bool(info.mute)


class PulseAudio(GObject.Object):
    """Tracks the default sink's volume/mute and lets you change them."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "ready": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.available = False
        self.sink = None            # Sink | None
        self.sinks = []             # all sinks, refreshed lazily
        self.mic_mute = False
        self.mic_index = PA_INVALID_INDEX
        self.mic_description = ""
        self._default_sink_name = None
        self._default_source_name = None
        self._ctx = None
        self._ml = None
        self._pa = None
        # ctypes trampolines must outlive the C calls that store them.
        self._keep = []
        self._reconnect_id = 0
        self._connect()

    # -- connection --------------------------------------------------------
    def _connect(self):
        try:
            self._pa, self._glue = _load()
        except OSError:
            return
        self._ml = self._glue.pa_glib_mainloop_new(None)
        api = self._glue.pa_glib_mainloop_get_api(self._ml)
        self._ctx = self._pa.pa_context_new(api, b"taldock")
        if not self._ctx:
            return
        self._cb_state = CTX_STATE_CB(self._on_state)
        self._cb_subscribe = SUBSCRIBE_CB(self._on_event)
        self._cb_sink = SINK_CB(self._on_sink)
        self._cb_sink_list = SINK_CB(self._on_sink_list)
        self._cb_source = SOURCE_CB(self._on_source)
        self._cb_server = SERVER_CB(self._on_server)
        self._cb_success = SUCCESS_CB(lambda *_a: None)
        self._keep += [self._cb_state, self._cb_subscribe, self._cb_sink,
                       self._cb_sink_list, self._cb_source, self._cb_server,
                       self._cb_success]
        self._pa.pa_context_set_state_callback(self._ctx, self._cb_state, None)
        # NOFLAGS: never try to autospawn a daemon we do not own.
        self._pa.pa_context_connect(self._ctx, None, 0, None)

    def _on_state(self, ctx, _ud):
        state = self._pa.pa_context_get_state(ctx)
        if state == PA_CONTEXT_READY:
            self.available = True
            self._pa.pa_context_set_subscribe_callback(ctx, self._cb_subscribe, None)
            self._unref(self._pa.pa_context_subscribe(
                ctx, PA_SUBSCRIPTION_MASK_SINK | PA_SUBSCRIPTION_MASK_SERVER
                | PA_SUBSCRIPTION_MASK_SOURCE, self._cb_success, None))
            self.refresh()
            self.emit("ready")
        elif state in (PA_CONTEXT_FAILED, PA_CONTEXT_TERMINATED):
            self.available = False
            self.emit("changed")
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        """The sound server can restart underneath us; retry gently."""
        if self._reconnect_id:
            return

        def retry():
            self._reconnect_id = 0
            self._teardown()
            self._connect()
            return GLib.SOURCE_REMOVE

        self._reconnect_id = GLib.timeout_add_seconds(5, retry)

    def _teardown(self):
        if self._ctx and self._pa:
            self._pa.pa_context_disconnect(self._ctx)
            self._pa.pa_context_unref(self._ctx)
            self._ctx = None
        if self._ml:
            self._glue.pa_glib_mainloop_free(self._ml)
            self._ml = None

    def _unref(self, op):
        if op:
            self._pa.pa_operation_unref(op)

    # -- server callbacks --------------------------------------------------
    def _on_event(self, _ctx, _event, _idx, _ud):
        # Any sink/server change: just re-read, it is one cheap round trip.
        self.refresh()

    def _on_server(self, _ctx, info_p, _ud):
        if not info_p:
            return
        info = info_p.contents
        self._default_sink_name = info.default_sink_name
        self._default_source_name = info.default_source_name
        if self._default_sink_name:
            self._unref(self._pa.pa_context_get_sink_info_by_name(
                self._ctx, self._default_sink_name, self._cb_sink, None))
        if self._default_source_name:
            self._unref(self._pa.pa_context_get_source_info_by_name(
                self._ctx, self._default_source_name, self._cb_source, None))

    def _on_sink(self, _ctx, info_p, eol, _ud):
        if eol or not info_p:
            return
        self.sink = Sink(info_p.contents)
        self.emit("changed")

    def _on_sink_list(self, _ctx, info_p, eol, _ud):
        if eol:
            self.emit("changed")
            return
        if info_p:
            self.sinks.append(Sink(info_p.contents))

    def _on_source(self, _ctx, info_p, eol, _ud):
        if eol or not info_p:
            return
        was = (self.mic_mute, self.mic_index, self.mic_description)
        self.mic_mute = bool(info_p.contents.mute)
        self.mic_index = int(info_p.contents.index)
        self.mic_description = (info_p.contents.description
                                or b"").decode("utf-8", "replace")
        if (self.mic_mute, self.mic_index, self.mic_description) != was:
            # Without this the bar only repainted on a mic change by luck,
            # when the sink list that follows a refresh happened to emit
            # first -- and then with the old mic state still in hand.
            self.emit("changed")

    # -- api ---------------------------------------------------------------
    def refresh(self):
        if not (self.available and self._ctx):
            return
        self.sinks = []
        self._unref(self._pa.pa_context_get_server_info(
            self._ctx, self._cb_server, None))
        self._unref(self._pa.pa_context_get_sink_info_list(
            self._ctx, self._cb_sink_list, None))

    @property
    def volume(self):
        return self.sink.volume if self.sink else 0.0

    @property
    def muted(self):
        return self.sink.mute if self.sink else True

    @property
    def mic_live(self):
        """True when an input exists and is not muted.

        `mic_mute` alone is not enough: it starts False, so a machine with
        no input at all would claim a live microphone until the first
        source arrives -- or for ever, if none does.
        """
        return (self.available and self.mic_index != PA_INVALID_INDEX
                and not self.mic_mute)

    def set_volume(self, value):
        if not (self.available and self.sink):
            return
        value = max(0.0, min(1.5, value))
        cvol = PaCVolume()
        cvol.channels = self.sink.channels
        raw = int(round(value * PA_VOLUME_NORM))
        for i in range(self.sink.channels):
            cvol.values[i] = raw
        # Update locally first so the UI tracks the drag without waiting.
        self.sink.volume = value
        self.emit("changed")
        self._unref(self._pa.pa_context_set_sink_volume_by_index(
            self._ctx, self.sink.index, C.byref(cvol), self._cb_success, None))

    def step_volume(self, delta):
        self.set_volume(self.volume + delta)

    def set_mute(self, muted):
        if not (self.available and self.sink):
            return
        self.sink.mute = bool(muted)
        self.emit("changed")
        self._unref(self._pa.pa_context_set_sink_mute_by_index(
            self._ctx, self.sink.index, 1 if muted else 0, self._cb_success, None))

    def toggle_mute(self):
        self.set_mute(not self.muted)

    def set_mic_mute(self, muted):
        if not (self.available and self.mic_index != PA_INVALID_INDEX):
            return
        self.mic_mute = bool(muted)
        self.emit("changed")
        self._unref(self._pa.pa_context_set_source_mute_by_index(
            self._ctx, self.mic_index, 1 if muted else 0, self._cb_success, None))

    def set_default_sink(self, name):
        if not (self.available and self._ctx):
            return
        self._unref(self._pa.pa_context_set_default_sink(
            self._ctx, name.encode(), self._cb_success, None))
