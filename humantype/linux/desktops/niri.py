"""niri: layouts and the focused window through its IPC socket (JSON, one message per line),
the field through AT-SPI.

The event stream carries everything the daemon needs: niri sends the full window, workspace and
layout lists on subscribing and every change after, so the windows are kept here from the events
alone. Dragging a window makes dozens of layout events a second, and the keyboard is served from
the same main loop: nothing on that path may wait on the socket.
"""

import glob
import json
import logging
import os
import socket

from gi.repository import GLib

from . import atspi
from .common import Backend, lang_of

log = logging.getLogger(__name__)

TIMEOUT_S = 0.3


def find_socket() -> str:
    """The service may start without NIRI_SOCKET in its environment: take niri's own socket."""
    path = os.environ.get("NIRI_SOCKET")
    if path and os.path.exists(path):
        return path
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    found = sorted(glob.glob(f"{runtime}/niri.*.sock"), key=os.path.getmtime)
    if not found:
        raise OSError("no niri IPC socket")
    return found[-1]


class Niri(Backend):
    name = "niri"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.path = find_socket()
        self._index = 0
        self.fields = atspi.make()
        self._events: socket.socket | None = None
        self._buffer = b""
        self._windows: dict[int, dict] = {}
        self._focused_id: int | None = None
        self._workspace_output: dict[int, str] = {}
        self._output_size: dict[str, tuple[int, int]] = {}
        GLib.idle_add(self._connect)

    def _request(self, request):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT_S)
            sock.connect(self.path)
            sock.sendall((json.dumps(request) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        reply = json.loads(data or b"null")
        if not isinstance(reply, dict) or "Ok" not in reply:
            raise OSError(f"niri: {reply!r}")
        return reply["Ok"]

    def _connect(self) -> bool:
        try:
            self.path = find_socket()
            self._set_layouts(self._request("KeyboardLayouts")["KeyboardLayouts"])
            self._read_outputs()
            self._events = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._events.connect(self.path)
            self._events.sendall(b'"EventStream"\n')
            self._buffer = b""
            GLib.io_add_watch(self._events.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                              self._on_events)
        except (OSError, ValueError, KeyError, TypeError) as error:
            log.warning("niri IPC: %s; trying again", error)
            if self._events is not None:
                self._events.close()
                self._events = None
            GLib.timeout_add_seconds(3, self._connect)
            return GLib.SOURCE_REMOVE
        self._appeared()
        return GLib.SOURCE_REMOVE

    def _read_outputs(self) -> None:
        """Output sizes, to tell a fullscreen window: niri does not say it outright."""
        outputs = self._request("Outputs")["Outputs"]
        self._output_size = {name: (o["logical"]["width"], o["logical"]["height"])
                             for name, o in outputs.items() if o.get("logical")}

    # --- layouts ---

    def _set_layouts(self, layouts: dict) -> None:
        self._langs = [lang_of(name) for name in layouts.get("names", [])]
        self._set_index(layouts.get("current_idx", 0))

    def _set_index(self, index: int) -> None:
        self._index = index
        self._layout_now(self._langs[index] if 0 <= index < len(self._langs) else None)

    def _current(self) -> str | None:
        # The event stream reports every switch, from any keyboard and any bind.
        return self._langs[self._index] if 0 <= self._index < len(self._langs) else None

    def _switch(self, lang: str) -> bool:
        if lang not in self._langs:
            return False
        index = self._langs.index(lang)
        if self._request({"Action": {"SwitchLayout": {"layout": {"Index": index}}}}) != "Handled":
            return False
        self._index = index  # at once: the event saying so comes a little later
        return True

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
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                try:
                    self._event(event)
                except (ValueError, KeyError, TypeError, IndexError) as error:
                    log.debug("niri event %s: %s", next(iter(event), "?"), error)
        return GLib.SOURCE_CONTINUE

    def _event(self, event: dict) -> None:
        if "KeyboardLayoutsChanged" in event:
            self._set_layouts(event["KeyboardLayoutsChanged"]["keyboard_layouts"])
        elif "KeyboardLayoutSwitched" in event:
            self._set_index(event["KeyboardLayoutSwitched"]["idx"])
        elif "WindowsChanged" in event:
            windows = event["WindowsChanged"]["windows"]
            self._windows = {w["id"]: w for w in windows}
            self._focused_id = next((w["id"] for w in windows if w.get("is_focused")), None)
            self._focus_changed()
        elif "WindowOpenedOrChanged" in event:
            win = event["WindowOpenedOrChanged"]["window"]
            self._windows[win["id"]] = win
            if win.get("is_focused"):
                self._focused_id = win["id"]
            if win["id"] == self._focused_id:
                self._focus_changed()
        elif "WindowClosed" in event:
            self._windows.pop(event["WindowClosed"]["id"], None)
        elif "WindowFocusChanged" in event:
            self._focused_id = event["WindowFocusChanged"]["id"]
            self._focus_changed()
        elif "WindowLayoutsChanged" in event:
            for wid, layout in event["WindowLayoutsChanged"]["changes"]:
                if wid in self._windows:
                    self._windows[wid]["layout"] = layout
                    if wid == self._focused_id:
                        self._focus_changed()
        elif "WorkspacesChanged" in event:
            # Also what a monitor plugged in or out looks like from here.
            workspaces = event["WorkspacesChanged"]["workspaces"]
            self._workspace_output = {w["id"]: w.get("output") for w in workspaces}
            if set(self._workspace_output.values()) - set(self._output_size) - {None}:
                try:
                    self._read_outputs()
                except (OSError, ValueError, KeyError, TypeError) as error:
                    log.debug("niri outputs: %s", error)

    def _lost(self) -> bool:
        if self._events is not None:
            self._events.close()
        self._events = None
        self._vanished()
        GLib.timeout_add_seconds(3, self._connect)
        return GLib.SOURCE_REMOVE

    def _focus_changed(self) -> None:
        win = self._windows.get(self._focused_id) or {}
        app = win.get("app_id") or ""
        self._window_now(app, app, self._fullscreen(win), win.get("pid") or 0)

    def _fullscreen(self, win: dict) -> bool:
        """A window as large as its monitor. Maximized to the edges stops short of the bar."""
        size = (win.get("layout") or {}).get("window_size")
        output = self._workspace_output.get(win.get("workspace_id"))
        full = self._output_size.get(output)
        return bool(size and full) and size[0] >= full[0] and size[1] >= full[1]
