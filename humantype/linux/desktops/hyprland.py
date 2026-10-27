"""Hyprland: layouts and the focused window through its two sockets (requests and events),
the field through AT-SPI."""

import json
import logging
import os
import socket
from pathlib import Path

from gi.repository import GLib

from . import atspi
from .common import Backend, lang_of

log = logging.getLogger(__name__)

TIMEOUT_S = 0.3


def socket_dir() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "hypr"
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:  # the service may start without it: take the newest instance
        found = sorted(base.glob("*/.socket.sock"), key=lambda p: p.stat().st_mtime)
        if not found:
            raise OSError("no Hyprland instance")
        return found[-1].parent
    return base / sig if (base / sig).exists() else Path("/tmp/hypr") / sig  # before Hyprland 0.40


class Hyprland(Backend):
    name = "Hyprland"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.dir = socket_dir()
        self._langs: list[str | None] = []
        self.fields = atspi.make()
        self._events: socket.socket | None = None
        self._buffer = b""
        GLib.idle_add(self._connect)

    def _request(self, command: str) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT_S)
            sock.connect(str(self.dir / ".socket.sock"))
            sock.sendall(command.encode())
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
            return b"".join(chunks).decode(errors="replace")

    def _json(self, command: str):
        return json.loads(self._request(f"j/{command}"))

    def _connect(self) -> bool:
        try:
            self._read_layouts()
            self._layout_now(self._current())
            self._focused()
            self._events = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._events.connect(str(self.dir / ".socket2.sock"))
            GLib.io_add_watch(self._events.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                              self._on_events)
        except (OSError, ValueError) as error:
            log.warning("Hyprland IPC: %s; trying again", error)
            GLib.timeout_add_seconds(3, self._connect)
            return GLib.SOURCE_REMOVE
        self._appeared()
        return GLib.SOURCE_REMOVE

    # --- layouts ---

    def _read_layouts(self) -> None:
        option = self._json("getoption input:kb_layout")
        self._langs = [lang_of(code) for code in (option.get("str") or "us").split(",")]
        log.info("Hyprland layouts: %s", self._langs)

    def _main_keyboard(self) -> dict | None:
        keyboards = self._json("devices").get("keyboards", [])
        return next((k for k in keyboards if k.get("main")), keyboards[0] if keyboards else None)

    def _current(self) -> str | None:
        try:
            kb = self._main_keyboard()
        except (OSError, ValueError):
            return self._lang
        if kb is None:
            return None
        index = kb.get("active_layout_index")
        if isinstance(index, int) and 0 <= index < len(self._langs):
            return self._langs[index]
        return lang_of(kb.get("active_keymap", ""))

    def _switch(self, lang: str) -> bool:
        if lang not in self._langs:
            return False
        return self._request(f"switchxkblayout all {self._langs.index(lang)}").strip() == "ok"

    # --- events ---

    def _on_events(self, _fd, condition) -> bool:
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            return self._lost()
        try:
            data = self._events.recv(65536)
        except OSError:
            return self._lost()
        if not data:
            return self._lost()
        self._buffer += data
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            name, _, value = line.decode(errors="replace").partition(">>")
            if name == "activelayout":
                self._layout_now(lang_of(value.rpartition(",")[2]))
            elif name in ("activewindowv2", "fullscreen"):
                try:
                    self._focused()
                except (OSError, ValueError) as error:
                    log.debug("activewindow: %s", error)
            elif name == "configreloaded":
                try:
                    self._read_layouts()
                except (OSError, ValueError) as error:
                    log.debug("layouts: %s", error)
        return GLib.SOURCE_CONTINUE

    def _lost(self) -> bool:
        self._events.close()
        self._events = None
        self._vanished()
        GLib.timeout_add_seconds(3, self._connect)
        return GLib.SOURCE_REMOVE

    def _focused(self) -> None:
        win = self._json("activewindow") or {}
        cls = win.get("class") or win.get("initialClass") or ""
        self._window_now(cls, cls, bool(win.get("fullscreen")), win.get("pid") or 0)
