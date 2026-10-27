"""KDE Plasma (Wayland or X11): layouts through the keyboard daemon's D-Bus (org.kde.keyboard),
the focused window through a small KWin script that calls us back, the field through AT-SPI."""

import logging
from pathlib import Path

from gi.repository import Gio, GLib

from . import atspi
from .common import Backend, lang_of

log = logging.getLogger(__name__)

KB_NAME, KB_PATH, KB_IFACE = "org.kde.keyboard", "/Layouts", "org.kde.KeyboardLayouts"
SCRIPT_NAME = "humantype"
SCRIPT_FILE = Path(__file__).with_name("kwin_script.js")
CALL_TIMEOUT_MS = 300


class Kde(Backend):
    name = "KDE Plasma"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._langs: list[str | None] = []
        self.fields = atspi.make()
        self.bus.signal_subscribe(KB_NAME, KB_IFACE, None, KB_PATH, None, Gio.DBusSignalFlags.NONE, self._signal)
        Gio.bus_watch_name_on_connection(self.bus, KB_NAME, Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self._kb_here(), lambda *_: self._vanished())
        Gio.bus_watch_name_on_connection(self.bus, "org.kde.KWin", Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self._load_script(), None)

    def _call(self, name: str, path: str, iface: str, method: str, params=None, reply: str | None = None):
        result = self.bus.call_sync(name, path, iface, method, params, GLib.VariantType(reply) if reply else None,
                                    Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, None)
        return result.unpack() if result is not None else None

    # --- layouts ---

    def _kb_here(self) -> None:
        try:
            self._read_layouts()
            self._layout_now(self._current())
        except GLib.Error as error:
            log.warning("KDE keyboard layouts: %s", error.message)
            return
        self._appeared()

    def _read_layouts(self) -> None:
        layouts = self._call(KB_NAME, KB_PATH, KB_IFACE, "getLayoutsList", None, "(a(sss))")[0]
        self._langs = [lang_of(short) or lang_of(long) for short, _variant, long in layouts]
        log.info("KDE layouts: %s", self._langs)

    def _current(self) -> str | None:
        try:
            index = self._call(KB_NAME, KB_PATH, KB_IFACE, "getLayout", None, "(u)")[0]
        except GLib.Error:
            return self._lang
        return self._langs[index] if index < len(self._langs) else None

    def _switch(self, lang: str) -> bool:
        if lang not in self._langs:
            return False
        ok = self._call(KB_NAME, KB_PATH, KB_IFACE, "setLayout", GLib.Variant("(u)", (self._langs.index(lang),)), "(b)")
        return bool(ok and ok[0])

    def _signal(self, _conn, _sender, _path, _iface, name, params) -> None:
        if name == "layoutChanged":
            index = params.unpack()[0]
            self._layout_now(self._langs[index] if index < len(self._langs) else None)
        elif name == "layoutListChanged":
            try:
                self._read_layouts()
            except GLib.Error as error:
                log.warning("KDE keyboard layouts: %s", error.message)

    # --- the focused window, from the KWin script ---

    def _load_script(self) -> None:
        scripting = ("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting")
        try:
            if self._call(*scripting, "isScriptLoaded", GLib.Variant("(s)", (SCRIPT_NAME,)), "(b)")[0]:
                self._call(*scripting, "unloadScript", GLib.Variant("(s)", (SCRIPT_NAME,)), "(b)")
            self._call(*scripting, "loadScript", GLib.Variant("(ss)", (str(SCRIPT_FILE), SCRIPT_NAME)), "(i)")
            self._call(*scripting, "start")
            log.info("KWin script loaded")
        except GLib.Error as error:
            log.warning("KWin script: %s (app exclusions and full screen checks are off)", error.message)

    def window_changed(self, wm_class: str, app: str, fullscreen: bool, pid: int) -> None:
        self._window_now(app.removesuffix(".desktop"), wm_class, fullscreen, pid)
