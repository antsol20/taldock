"""NetworkManager state over GDBus. Event-driven, no polling."""
from __future__ import annotations

from gi.repository import Gio, GLib, GObject

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"

DEV_ETHERNET, DEV_WIFI = 1, 2
NM_DEVICE_STATE_ACTIVATED = 100
# NM_802_11_AP_SEC flags
SEC_NONE = 0x0
SEC_KEY_MGMT_PSK = 0x100
SEC_KEY_MGMT_802_1X = 0x200
SEC_KEY_MGMT_SAE = 0x400
# NM_ACTIVE_CONNECTION_STATE
ACTIVE_ACTIVATED = 2
ACTIVE_DEACTIVATED = 4
# How long to wait for an association before calling it a failure. Long
# enough for a slow DHCP lease, short enough not to spin for ever.
CONNECT_TIMEOUT_S = 30


def _proxy(path, iface):
    try:
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
            NM, path, iface, None)
    except GLib.Error:
        return None


def _prop(proxy, name, default=None):
    if proxy is None:
        return default
    val = proxy.get_cached_property(name)
    return val.unpack() if val is not None else default


def _ssid_text(raw):
    """NM hands SSIDs over as a byte array."""
    if not raw:
        return ""
    return bytes(raw).decode("utf-8", "replace")


class AccessPoint:
    __slots__ = ("path", "ssid", "strength", "security", "freq", "active")

    def __init__(self, path, ssid, strength, security, freq, active=False):
        self.path = path
        self.ssid = ssid
        self.strength = strength
        # "none" | "psk" | "sae" | "other". Only the first three can be
        # joined from the dock; "other" (802.1X, WEP, OWE) needs settings we
        # do not ask for, and goes to nm-connection-editor.
        self.security = security
        self.freq = freq
        self.active = active

    @property
    def secure(self):
        return self.security != "none"

    @property
    def joinable(self):
        return self.security in ("none", "psk", "sae")

    @property
    def band(self):
        return "5 GHz" if self.freq > 4000 else "2.4 GHz"


def _security_of(wpa_flags, rsn_flags):
    """Classify an AP from its WPA/RSN key-management bits."""
    both = wpa_flags | rsn_flags
    # A WPA2/WPA3 transitional network advertises both; SAE is the one we
    # must ask NM for, because joining it as wpa-psk fails on a WPA3-only AP.
    if rsn_flags & SEC_KEY_MGMT_SAE:
        return "sae"
    if both & SEC_KEY_MGMT_802_1X:
        return "other"
    if both & SEC_KEY_MGMT_PSK:
        return "psk"
    if both:
        return "other"      # WEP, OWE: encrypted, but not by a passphrase
    return "none"


class NetworkMonitor(GObject.Object):
    """Tracks the primary connection and, if it is wifi, its signal."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self):
        super().__init__()
        self.kind = "none"          # wifi | ethernet | other | none
        self.ssid = ""
        self.strength = 0
        self.ifname = ""
        self.ip4 = ""
        self.wifi_enabled = True
        self.available = False

        self._wifi_dev = None       # (path, Device proxy, Wireless proxy)
        self._ap_proxy = None
        self._ap_handler = 0

        self.manager = _proxy(NM_PATH, NM)
        if self.manager is None:
            return
        self.available = True
        self.manager.connect("g-properties-changed", self._on_manager_changed)
        self.refresh()

    # -- state -------------------------------------------------------------
    def _on_manager_changed(self, *_a):
        self.refresh()

    def _find_devices(self):
        wifi = ethernet = None
        for path in _prop(self.manager, "Devices", []) or []:
            dev = _proxy(path, NM + ".Device")
            dtype = _prop(dev, "DeviceType", 0)
            if dtype == DEV_WIFI and wifi is None:
                wifi = (path, dev, _proxy(path, NM + ".Device.Wireless"))
            elif dtype == DEV_ETHERNET and ethernet is None:
                ethernet = (path, dev, None)
        return wifi, ethernet

    def refresh(self):
        if not self.available:
            return
        self.wifi_enabled = bool(_prop(self.manager, "WirelessEnabled", True))
        wifi, ethernet = self._find_devices()
        self._wifi_dev = wifi
        ctype = _prop(self.manager, "PrimaryConnectionType", "") or ""

        self.ssid, self.strength, self.ifname, self.ip4 = "", 0, "", ""
        if ctype.startswith("802-11-wireless"):
            self.kind = "wifi"
            self._bind_active_ap(wifi)
            if wifi:
                self.ifname = _prop(wifi[1], "Interface", "") or ""
                self.ip4 = self._ip4_for(wifi[1])
        elif ctype.startswith("802-3-ethernet"):
            self.kind = "ethernet"
            self._unbind_ap()
            if ethernet:
                self.ifname = _prop(ethernet[1], "Interface", "") or ""
                self.ip4 = self._ip4_for(ethernet[1])
        elif ctype:
            self.kind = "other"
            self._unbind_ap()
        else:
            self.kind = "none"
            self._unbind_ap()
        self.emit("changed")

    def _ip4_for(self, dev):
        cfg_path = _prop(dev, "Ip4Config", "/")
        if not cfg_path or cfg_path == "/":
            return ""
        cfg = _proxy(cfg_path, NM + ".IP4Config")
        for entry in _prop(cfg, "AddressData", []) or []:
            addr = entry.get("address")
            if addr:
                return addr
        return ""

    # -- live signal strength ---------------------------------------------
    def _bind_active_ap(self, wifi):
        """Follow the active AP's Strength property instead of polling it."""
        ap_path = _prop(wifi[2], "ActiveAccessPoint", "/") if wifi else "/"
        if not ap_path or ap_path == "/":
            self._unbind_ap()
            return
        if self._ap_proxy is not None and \
                self._ap_proxy.get_object_path() == ap_path:
            self._read_ap()
            return
        self._unbind_ap()
        self._ap_proxy = _proxy(ap_path, NM + ".AccessPoint")
        if self._ap_proxy is not None:
            self._ap_handler = self._ap_proxy.connect(
                "g-properties-changed", self._on_ap_changed)
            self._read_ap()

    def _unbind_ap(self):
        if self._ap_proxy is not None and self._ap_handler:
            self._ap_proxy.disconnect(self._ap_handler)
        self._ap_proxy = None
        self._ap_handler = 0

    def _read_ap(self):
        self.ssid = _ssid_text(_prop(self._ap_proxy, "Ssid", b""))
        self.strength = int(_prop(self._ap_proxy, "Strength", 0) or 0)

    def _on_ap_changed(self, _proxy, changed, *_a):
        self._read_ap()
        self.emit("changed")

    # -- scanning ----------------------------------------------------------
    def access_points(self):
        """Visible APs, strongest first, deduplicated by SSID."""
        if not self._wifi_dev:
            return []
        active = _prop(self._wifi_dev[2], "ActiveAccessPoint", "/")
        best = {}
        for path in _prop(self._wifi_dev[2], "AccessPoints", []) or []:
            ap = _proxy(path, NM + ".AccessPoint")
            ssid = _ssid_text(_prop(ap, "Ssid", b""))
            if not ssid:
                continue
            strength = int(_prop(ap, "Strength", 0) or 0)
            security = _security_of(_prop(ap, "WpaFlags", 0) or 0,
                                    _prop(ap, "RsnFlags", 0) or 0)
            entry = AccessPoint(path, ssid, strength, security,
                                int(_prop(ap, "Frequency", 0) or 0),
                                path == active)
            prev = best.get(ssid)
            # Keep the active BSSID even when a duplicate reports more signal,
            # otherwise the connected network loses its marker.
            if prev is None or (entry.active and not prev.active) or \
                    (strength > prev.strength and not prev.active):
                best[ssid] = entry
        return sorted(best.values(),
                      key=lambda a: (not a.active, -a.strength))

    def request_scan(self):
        if not self._wifi_dev or self._wifi_dev[2] is None:
            return
        try:
            self._wifi_dev[2].call_sync(
                "RequestScan", GLib.Variant("(a{sv})", ({},)),
                Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass   # NM rate-limits scans; not worth surfacing

    def set_wifi_enabled(self, enabled):
        if self.manager is None:
            return
        try:
            self.manager.call_sync(
                "org.freedesktop.DBus.Properties.Set",
                GLib.Variant("(ssv)", (NM, "WirelessEnabled",
                                       GLib.Variant("b", enabled))),
                Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass

    # -- saved profiles ----------------------------------------------------
    def _saved_wifi(self):
        """Yield (connection path, ssid) for every saved wireless profile.

        Walking Settings costs two round trips per connection, so callers
        that want more than one answer should walk once rather than call
        saved_connection_for() per network.
        """
        settings = _proxy(NM_PATH + "/Settings", NM + ".Settings")
        if settings is None:
            return
        try:
            paths = settings.call_sync("ListConnections", None,
                                       Gio.DBusCallFlags.NONE, 2000, None)[0]
        except GLib.Error:
            return
        for path in paths:
            conn = _proxy(path, NM + ".Settings.Connection")
            if conn is None:
                continue
            try:
                cfg = conn.call_sync("GetSettings", None,
                                     Gio.DBusCallFlags.NONE, 2000, None)[0]
            except GLib.Error:
                continue
            ssid = _ssid_text((cfg.get("802-11-wireless") or {}).get("ssid", b""))
            if ssid:
                yield path, ssid

    def saved_connection_for(self, ssid):
        """Object path of a saved profile matching `ssid`, if any."""
        for path, saved in self._saved_wifi():
            if saved == ssid:
                return path
        return None

    def saved_ssids(self):
        """Every SSID we hold a profile for, in one pass."""
        return {ssid for _path, ssid in self._saved_wifi()}

    def forget(self, ssid):
        """Delete the saved profile for `ssid`. Returns True if one went."""
        path = self.saved_connection_for(ssid)
        if path is None:
            return False
        conn = _proxy(path, NM + ".Settings.Connection")
        if conn is None:
            return False
        try:
            conn.call_sync("Delete", None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            return False
        self.refresh()
        return True

    # -- connecting --------------------------------------------------------
    def activate(self, ap):
        """Bring up an existing saved profile for `ap`. True if we tried."""
        conn = self.saved_connection_for(ap.ssid)
        if conn is None or not self._wifi_dev:
            return False
        try:
            self.manager.call_sync(
                "ActivateConnection",
                GLib.Variant("(ooo)", (conn, self._wifi_dev[0], ap.path)),
                Gio.DBusCallFlags.NONE, 5000, None)
            return True
        except GLib.Error:
            return False

    def connect_new(self, ap, password, on_done):
        """Create a profile for `ap` and bring it up. Asynchronous.

        No secret agent is involved: the passphrase goes into the profile
        with default secret flags, so NetworkManager stores it itself and
        never has to ask anyone for it again -- which is exactly what
        `nmcli device wifi connect <ssid> password <pw>` does.

        `on_done(ok, message)` is called exactly once, and not before the
        network is actually up or actually failed.
        """
        if self.manager is None or not self._wifi_dev:
            on_done(False, "No Wi-Fi device")
            return
        cfg = {
            "connection": {
                # NM generates the uuid; supplying one only risks a clash.
                "id": GLib.Variant("s", ap.ssid),
                "type": GLib.Variant("s", "802-11-wireless"),
            },
            "802-11-wireless": {
                "ssid": GLib.Variant("ay", ap.ssid.encode("utf-8")),
                "mode": GLib.Variant("s", "infrastructure"),
            },
        }
        if ap.security in ("psk", "sae"):
            cfg["802-11-wireless-security"] = {
                "key-mgmt": GLib.Variant(
                    "s", "sae" if ap.security == "sae" else "wpa-psk"),
                "psk": GLib.Variant("s", password),
            }

        attempt = _ConnectAttempt(self, ap.ssid, on_done)

        def replied(proxy, result, _data):
            try:
                conn_path, active_path = proxy.call_finish(result).unpack()
            except GLib.Error as exc:
                attempt.finish(False, exc.message.rsplit(": ", 1)[-1])
                return
            attempt.watch(conn_path, active_path)

        self.manager.call(
            "AddAndActivateConnection",
            GLib.Variant("(a{sa{sv}}oo)", (cfg, self._wifi_dev[0], ap.path)),
            Gio.DBusCallFlags.NONE, CONNECT_TIMEOUT_S * 1000, None,
            replied, None)


class _ConnectAttempt:
    """Watches one AddAndActivateConnection through to a verdict.

    NM answers the method call as soon as it has accepted the request, not
    when the network is up, so the verdict has to come from the
    ActiveConnection's State afterwards.

    A failed attempt must take its half-made profile with it. The passphrase
    is stored by NM with no secret agent anywhere, so a mistyped one is saved
    silently -- and every later attempt would then find that profile through
    saved_connection_for(), reuse the wrong passphrase and fail the same way,
    with nothing ever asking again. The network would simply stop being
    joinable from the dock.
    """

    def __init__(self, monitor, ssid, on_done):
        self.monitor = monitor
        self.ssid = ssid
        self.on_done = on_done
        self.conn_path = None
        self._proxy = None
        self._handler = 0
        self._timeout = GLib.timeout_add_seconds(
            CONNECT_TIMEOUT_S, self._on_timeout)

    def watch(self, conn_path, active_path):
        self.conn_path = conn_path
        if self.on_done is None:
            return                      # already timed out
        self._proxy = _proxy(active_path, NM + ".Connection.Active")
        if self._proxy is None:
            self.finish(False, "Could not follow the connection")
            return
        self._handler = self._proxy.connect("g-properties-changed",
                                            self._on_changed)
        self._check()

    def _on_changed(self, *_a):
        self._check()

    def _check(self):
        state = _prop(self._proxy, "State", 0) or 0
        if state == ACTIVE_ACTIVATED:
            self.finish(True, "")
        elif state == ACTIVE_DEACTIVATED:
            # NM does not tell us *why* on the active connection, and with no
            # secret agent a bad passphrase is much the likeliest reason.
            self.finish(False, "Could not connect — wrong password?")

    def _on_timeout(self):
        self._timeout = 0
        self.finish(False, "Timed out")
        return GLib.SOURCE_REMOVE

    def finish(self, ok, message):
        if self.on_done is None:
            return                      # verdict already delivered
        done, self.on_done = self.on_done, None
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
        if self._proxy is not None and self._handler:
            self._proxy.disconnect(self._handler)
        self._proxy = None
        if not ok and self.conn_path:
            self._delete_profile()
        if ok:
            self.monitor.refresh()
        done(ok, message)

    def _delete_profile(self):
        conn = _proxy(self.conn_path, NM + ".Settings.Connection")
        if conn is None:
            return
        try:
            conn.call_sync("Delete", None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass
