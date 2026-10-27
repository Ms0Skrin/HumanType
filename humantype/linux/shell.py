"""Talking to the GNOME Shell extension (org.humantype.Shell) and offering our own small
D-Bus service (org.humantype.Daemon) for its quick settings toggle."""

import logging
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .engine import ShellState  # noqa: E402

log = logging.getLogger(__name__)

SHELL_NAME = "org.humantype.Shell"
SHELL_PATH = "/org/humantype/Shell"
DAEMON_NAME = "org.humantype.Daemon"
DAEMON_PATH = "/org/humantype/Daemon"
DAEMON_XML = """
<node>
  <interface name="org.humantype.Daemon">
    <method name="SetEnabled">
      <arg type="b" direction="in" name="on"/>
    </method>
    <property name="Enabled" type="b" access="read"/>
    <signal name="EnabledChanged">
      <arg type="b" name="on"/>
    </signal>
  </interface>
</node>
"""
CALL_TIMEOUT_MS = 300


class ShellClient:
    """Calls into the extension. Every call is synchronous with a short timeout: the keyboard
    waits meanwhile, so a shell that does not answer must not hold it for long."""

    def __init__(self, on_layout: Callable[[str | None], None], on_focus: Callable[[], None],
                 on_appeared: Callable[[], None], on_vanished: Callable[[], None] = lambda: None) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.present = False
        self._on_layout = on_layout
        self._on_focus = on_focus
        self._on_appeared = on_appeared
        self._on_vanished = on_vanished
        self.bus.signal_subscribe(SHELL_NAME, "org.humantype.Shell", None, SHELL_PATH, None,
                                  Gio.DBusSignalFlags.NONE, self._signal)
        Gio.bus_watch_name_on_connection(self.bus, SHELL_NAME, Gio.BusNameWatcherFlags.NONE,
                                         self._appeared, self._vanished)

    def _appeared(self, *_args) -> None:
        self.present = True
        log.info("GNOME Shell extension is here")
        self._on_appeared()

    def _vanished(self, *_args) -> None:
        was = self.present
        self.present = False
        self._on_layout(None)
        if was:
            log.warning("GNOME Shell extension is gone")
            self._on_vanished()

    def _signal(self, _conn, _sender, _path, _iface, name, params) -> None:
        if name == "LayoutChanged":
            self._on_layout(params.unpack()[0] or None)
        elif name == "FocusChanged":
            self._on_focus()

    def _call(self, method: str, params: GLib.Variant | None, reply: str | None):
        if not self.present:
            return None
        try:
            result = self.bus.call_sync(SHELL_NAME, SHELL_PATH, "org.humantype.Shell", method, params,
                                        GLib.VariantType(reply) if reply else None,
                                        Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, None)
        except GLib.Error as error:
            log.warning("shell %s failed: %s", method, error.message)
            return None
        return result.unpack() if result is not None else None

    def state(self, expect: str = "", ignore_case: bool = False) -> ShellState | None:
        result = self._call("GetState", GLib.Variant("(sb)", (expect, ignore_case)), "(a{sv})")
        if result is None:
            return None
        d = result[0]
        return ShellState(
            layout=d.get("layout") or None,
            app=d.get("app", ""),
            wm_class=d.get("wm_class", ""),
            purpose=d.get("purpose", "normal"),
            private=d.get("private", False),
            fullscreen=d.get("fullscreen", False),
            text_known=d.get("text_known", False),
            matches=d.get("matches", False),
            at_edge=d.get("at_edge", False),
            common=d.get("common", 0),
            common_edge=d.get("common_edge", False),
        )

    def layout(self) -> str | None:
        state = self.state()
        return state.layout if state else None

    def set_layout(self, lang: str) -> bool:
        result = self._call("SetLayout", GLib.Variant("(s)", (lang,)), "(b)")
        return bool(result and result[0])

    def note(self, kind: str, text: str) -> None:
        if not self.present:
            return
        # Fire and forget: a note is not worth holding the keyboard for.
        self.bus.call(SHELL_NAME, SHELL_PATH, "org.humantype.Shell", "ShowNote",
                      GLib.Variant("(ss)", (kind, text)), None, Gio.DBusCallFlags.NO_AUTO_START, -1, None, None)


class DaemonService:
    """org.humantype.Daemon: lets the extension's toggle turn automatic switching on and off."""

    def __init__(self, bus: Gio.DBusConnection, get_enabled: Callable[[], bool], set_enabled: Callable[[bool], None]) -> None:
        self.bus = bus
        self._get = get_enabled
        self._set = set_enabled
        info = Gio.DBusNodeInfo.new_for_xml(DAEMON_XML).interfaces[0]
        self._reg = bus.register_object(DAEMON_PATH, info, self._method, self._property, None)
        self._own = Gio.bus_own_name_on_connection(bus, DAEMON_NAME, Gio.BusNameOwnerFlags.NONE, None,
                                                   lambda *_: log.error("another humantype service holds the D-Bus name"))

    def _method(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method == "SetEnabled":
            self._set(params.unpack()[0])
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _property(self, _conn, _sender, _path, _iface, name):
        if name == "Enabled":
            return GLib.Variant("b", self._get())
        return None

    def enabled_changed(self, on: bool) -> None:
        self.bus.emit_signal(None, DAEMON_PATH, "org.humantype.Daemon", "EnabledChanged", GLib.Variant("(b)", (on,)))
        self.bus.emit_signal(None, DAEMON_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                             GLib.Variant("(sa{sv}as)", ("org.humantype.Daemon", {"Enabled": GLib.Variant("b", on)}, [])))

    def close(self) -> None:
        self.bus.unregister_object(self._reg)
        Gio.bus_unown_name(self._own)
