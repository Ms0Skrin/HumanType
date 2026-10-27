"""The tray icon outside GNOME: a StatusNotifierItem, which KDE Plasma, Xfce, Cinnamon, LXQt and
waybar (Sway, Hyprland) show. A click turns automatic switching on and off; the icon is grey
while it is off. (GNOME has the extension's own icon in the top bar instead.)"""

import logging
import os
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

log = logging.getLogger(__name__)

WATCHER = ("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher")
PATH = "/StatusNotifierItem"
XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""


class Tray:
    def __init__(self, bus: Gio.DBusConnection, toggle: Callable[[], None], icon_dir: str) -> None:
        self.bus = bus
        self._toggle = toggle
        self._icon_dir = icon_dir  # where humantype.svg and humantype-off.svg are, if not in the icon theme
        self.on = True
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self._reg = bus.register_object(PATH, info, self._method, self._property, None)
        self._own = Gio.bus_own_name_on_connection(bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        # Register now and again whenever a tray (watcher) comes up: panels restart.
        self._watch = Gio.bus_watch_name_on_connection(bus, WATCHER[0], Gio.BusNameWatcherFlags.NONE,
                                                       lambda *_: self._register(), None)

    def _register(self) -> None:
        self.bus.call(*WATCHER, "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.name,)), None,
                      Gio.DBusCallFlags.NONE, 2000, None, self._registered)

    def _registered(self, bus, result) -> None:
        try:
            bus.call_finish(result)
            log.info("tray icon is up")
        except GLib.Error as error:
            log.warning("tray icon: %s", error.message)

    def _text(self) -> str:
        return "Автопереключение включено" if self.on else "Автопереключение выключено"

    def _method(self, _conn, _sender, _path, _iface, method, _params, invocation) -> None:
        if method in ("Activate", "SecondaryActivate", "ContextMenu"):
            self._toggle()
        invocation.return_value(None)

    def _property(self, _conn, _sender, _path, _iface, name):
        icon = "humantype" if self.on else "humantype-off"
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "humantype"),
            "Title": GLib.Variant("s", "HumanType"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", icon),
            "IconThemePath": GLib.Variant("s", self._icon_dir),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (icon, [], "HumanType", self._text() + "\nНажмите, чтобы переключить")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/NO_DBUSMENU"),
        }
        return values.get(name)

    def show(self, on: bool) -> None:
        if on == self.on:
            return
        self.on = on
        for signal in ("NewIcon", "NewToolTip"):
            self.bus.emit_signal(None, PATH, "org.kde.StatusNotifierItem", signal, None)

    def close(self) -> None:
        Gio.bus_unwatch_name(self._watch)
        Gio.bus_unown_name(self._own)
        self.bus.unregister_object(self._reg)
