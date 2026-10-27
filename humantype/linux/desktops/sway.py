"""Sway (and other i3-IPC compositors): layouts and the focused window through the IPC socket,
the field through AT-SPI."""

import glob
import json
import logging
import os
import socket
import struct

from gi.repository import GLib

from . import atspi
from .common import Backend, lang_of

log = logging.getLogger(__name__)

MAGIC = b"i3-ipc"
HEADER = struct.Struct("=6sII")
RUN_COMMAND, SUBSCRIBE, GET_TREE, GET_INPUTS = 0, 2, 4, 100
EVENT_WINDOW, EVENT_INPUT = 0x80000003, 0x80000015
TIMEOUT_S = 0.3


def _send(sock: socket.socket, kind: int, payload: str = "") -> None:
    data = payload.encode()
    sock.sendall(HEADER.pack(MAGIC, len(data), kind) + data)


def _recv(sock: socket.socket) -> tuple[int, object]:
    head = _read(sock, HEADER.size)
    magic, length, kind = HEADER.unpack(head)
    if magic != MAGIC:
        raise OSError("not an i3-ipc reply")
    return kind, json.loads(_read(sock, length) or b"null")


def _read(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("the compositor closed the socket")
        buf += chunk
    return buf


def _find_socket() -> str:
    """The service may start without SWAYSOCK in its environment: take sway's own socket."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    found = sorted(glob.glob(f"{runtime}/sway-ipc.{os.getuid()}.*.sock"), key=os.path.getmtime)
    if not found:
        raise OSError("no sway IPC socket")
    return found[-1]


class Sway(Backend):
    name = "Sway"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.path = os.environ.get("SWAYSOCK") or _find_socket()
        self._langs: list[str | None] = []
        self.fields = atspi.make()
        self._events: socket.socket | None = None
        GLib.idle_add(self._connect)

    def _request(self, kind: int, payload: str = ""):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT_S)
            sock.connect(self.path)
            _send(sock, kind, payload)
            return _recv(sock)[1]

    def _connect(self) -> bool:
        try:
            self._read_inputs()
            self._events = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._events.connect(self.path)
            _send(self._events, SUBSCRIBE, json.dumps(["input", "window"]))
            _recv(self._events)
            GLib.io_add_watch(self._events.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                              self._on_events)
            self._focused_from_tree()
        except (OSError, ValueError) as error:
            log.warning("sway IPC: %s; trying again", error)
            GLib.timeout_add_seconds(3, self._connect)
            return GLib.SOURCE_REMOVE
        self._appeared()
        return GLib.SOURCE_REMOVE

    # --- layouts ---

    def _keyboard(self, inputs) -> dict | None:
        return next((i for i in inputs if i.get("type") == "keyboard" and i.get("xkb_layout_names")), None)

    def _read_inputs(self) -> None:
        kb = self._keyboard(self._request(GET_INPUTS))
        if kb is None:
            raise OSError("no keyboard in sway's inputs")
        self._langs = [lang_of(name) for name in kb["xkb_layout_names"]]
        index = kb.get("xkb_active_layout_index", 0)
        self._layout_now(self._langs[index] if 0 <= index < len(self._langs) else None)

    def _current(self) -> str | None:
        try:
            kb = self._keyboard(self._request(GET_INPUTS))
        except (OSError, ValueError):
            return self._lang
        if kb is None:
            return None
        index = kb.get("xkb_active_layout_index", 0)
        return self._langs[index] if 0 <= index < len(self._langs) else None

    def _switch(self, lang: str) -> bool:
        if lang not in self._langs:
            return False
        reply = self._request(RUN_COMMAND, f"input type:keyboard xkb_switch_layout {self._langs.index(lang)}")
        return bool(reply) and all(r.get("success") for r in reply)

    # --- events ---

    def _on_events(self, _fd, condition) -> bool:
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            return self._lost()
        try:
            kind, event = _recv(self._events)
        except (OSError, ValueError):
            return self._lost()
        if kind == EVENT_INPUT and event.get("change") in ("xkb_layout", "xkb_keymap"):
            kb = event.get("input", {})
            if kb.get("xkb_layout_names"):
                self._langs = [lang_of(name) for name in kb["xkb_layout_names"]]
                index = kb.get("xkb_active_layout_index", 0)
                self._layout_now(self._langs[index] if 0 <= index < len(self._langs) else None)
        elif kind == EVENT_WINDOW and event.get("change") in ("focus", "fullscreen_mode", "title"):
            self._window(event.get("container") or {})
        return GLib.SOURCE_CONTINUE

    def _lost(self) -> bool:
        self._events.close()
        self._events = None
        self._vanished()
        GLib.timeout_add_seconds(3, self._connect)
        return GLib.SOURCE_REMOVE

    def _window(self, node: dict) -> None:
        props = node.get("window_properties") or {}
        app = node.get("app_id") or ""
        self._window_now(app, props.get("class") or app, bool(node.get("fullscreen_mode")), node.get("pid") or 0)

    def _focused_from_tree(self) -> None:
        stack = [self._request(GET_TREE)]
        while stack:
            node = stack.pop()
            if node.get("focused") and node.get("type") in ("con", "floating_con"):
                self._window(node)
                return
            stack.extend(node.get("nodes", []) + node.get("floating_nodes", []))
