"""NetworkManager state over GDBus. Event-driven, no polling."""
from __future__ import annotations

from gi.repository import Gio, GLib, GObject

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"

DEV_ETHERNET, DEV_WIFI = 1, 2
NM_DEVICE_STATE_ACTIVATED = 100
# NM_802_11_AP_SEC flags
SEC_NONE = 0x0


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
    __slots__ = ("path", "ssid", "strength", "secure", "freq", "active")

    def __init__(self, path, ssid, strength, secure, freq, active=False):
        self.path = path
        self.ssid = ssid
        self.strength = strength
        self.secure = secure
        self.freq = freq
        self.active = active

    @property
    def band(self):
        return "5 GHz" if self.freq > 4000 else "2.4 GHz"


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
            secure = bool((_prop(ap, "WpaFlags", 0) or 0)
                          | (_prop(ap, "RsnFlags", 0) or 0))
            entry = AccessPoint(path, ssid, strength, secure,
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

    # -- connecting --------------------------------------------------------
    def saved_connection_for(self, ssid):
        """Object path of a saved profile matching `ssid`, if any."""
        settings = _proxy(NM_PATH + "/Settings", NM + ".Settings")
        if settings is None:
            return None
        try:
            paths = settings.call_sync("ListConnections", None,
                                       Gio.DBusCallFlags.NONE, 2000, None)[0]
        except GLib.Error:
            return None
        for path in paths:
            conn = _proxy(path, NM + ".Settings.Connection")
            if conn is None:
                continue
            try:
                cfg = conn.call_sync("GetSettings", None,
                                     Gio.DBusCallFlags.NONE, 2000, None)[0]
            except GLib.Error:
                continue
            wireless = cfg.get("802-11-wireless") or {}
            if _ssid_text(wireless.get("ssid", b"")) == ssid:
                return path
        return None

    def activate(self, ap):
        """Bring up a saved profile for `ap`. Returns True if we tried.

        New networks need a secret agent to prompt for the passphrase, which
        is the desktop's job, so those are handed to nm-connection-editor.
        """
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
