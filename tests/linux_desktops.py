"""The desktop backends against fake compositors: a fake sway IPC socket, fake Hyprland sockets and
a fake KDE keyboard service on a private D-Bus. No real desktop needed.

    dbus-run-session -- python tests/linux_desktops.py
"""

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from humantype.linux.desktops import atspi, detect  # noqa: E402
from humantype.linux.desktops.common import lang_of, text_check  # noqa: E402

atspi.make = lambda: None  # the fakes have no accessibility bus behind them

KB_XML = """<node><interface name="org.kde.KeyboardLayouts">
<method name="getLayout"><arg type="u" direction="out"/></method>
<method name="setLayout"><arg type="u" direction="in"/><arg type="b" direction="out"/></method>
<method name="getLayoutsList"><arg type="a(sss)" direction="out"/></method>
<signal name="layoutChanged"><arg type="u"/></signal>
<signal name="layoutListChanged"/>
</interface></node>"""


def fake_kde(index_file: str) -> None:
    """org.kde.keyboard as Plasma's keyboard daemon offers it, in a process of its own."""
    state = {"index": 1}

    def method(conn, _sender, path, iface, name, params, inv):
        if name == "getLayout":
            inv.return_value(GLib.Variant("(u)", (state["index"],)))
        elif name == "getLayoutsList":
            inv.return_value(GLib.Variant("(a(sss))", ([("us", "", "English (US)"), ("ru", "", "Russian")],)))
        elif name == "setLayout":
            state["index"] = params.unpack()[0]
            Path(index_file).write_text(str(state["index"]))
            inv.return_value(GLib.Variant("(b)", (True,)))
            conn.emit_signal(None, path, iface, "layoutChanged", GLib.Variant("(u)", (state["index"],)))

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.register_object("/Layouts", Gio.DBusNodeInfo.new_for_xml(KB_XML).interfaces[0], method, None, None)
    Gio.bus_own_name_on_connection(bus, "org.kde.keyboard", Gio.BusNameOwnerFlags.NONE, None, None)
    GLib.MainLoop().run()


if len(sys.argv) > 2 and sys.argv[1] == "--fake-kde":
    fake_kde(sys.argv[2])
    sys.exit(0)


fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


def pump(seconds=0.2, until=None):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.iteration(False)
        if until and until():
            return
        time.sleep(0.003)


class Calls:
    def __init__(self):
        self.layouts, self.focus, self.appeared, self.vanished = [], 0, 0, 0

    def args(self):
        return (self.layouts.append, self._focus, self._appeared, self._vanished)

    def _focus(self):
        self.focus += 1

    def _appeared(self):
        self.appeared += 1

    def _vanished(self):
        self.vanished += 1


# --- plain helpers ---

check("lang us", lang_of("us"), "en")
check("lang ru", lang_of("ru"), "ru")
check("lang us(intl)", lang_of("us(intl)"), "en")
check("lang name", lang_of("Russian"), "ru")
check("lang long name", lang_of("English (US)"), "en")
check("lang other", lang_of("de"), None)
t = text_check("мама ghbdtn", "ghbdtn", False)
check("text: matches at edge", (t["matches"], t["at_edge"], t["common"]), (True, True, 6))
t = text_check("xghbdtn", "helloghbdtn", False)
check("text: partly there", (t["matches"], t["common"], t["common_edge"]), (False, 6, False))
check("text: empty field", text_check("", "abc", False)["common_edge"], True)
for env, want in [({"XDG_CURRENT_DESKTOP": "KDE"}, "kde"), ({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, "gnome"),
                  ({"XDG_CURRENT_DESKTOP": "Hyprland"}, "hyprland"), ({"SWAYSOCK": "/x"}, "sway"),
                  ({"XDG_CURRENT_DESKTOP": "XFCE", "XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, "x11"),
                  ({"XDG_CURRENT_DESKTOP": "niri", "WAYLAND_DISPLAY": "wayland-1"}, "")]:
    saved = dict(os.environ)
    for k in ("XDG_CURRENT_DESKTOP", "HYPRLAND_INSTANCE_SIGNATURE", "SWAYSOCK", "XDG_SESSION_TYPE", "DISPLAY",
              "WAYLAND_DISPLAY", "HUMANTYPE_DESKTOP"):
        os.environ.pop(k, None)
    os.environ.update(env)
    check(f"detect {env}", detect(), want)
    os.environ.clear()
    os.environ.update(saved)

tmp = Path(tempfile.mkdtemp(prefix="humantype-desktops-"))

# --- sway ---

HEADER = struct.Struct("=6sII")


class FakeSway:
    def __init__(self, path):
        self.index = 0
        self.names = ["English (US)", "Russian"]
        self.subscribers = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen()
        threading.Thread(target=self.serve, daemon=True).start()

    def keyboard(self):
        return {"type": "keyboard", "identifier": "1:1:kb", "xkb_layout_names": self.names,
                "xkb_active_layout_index": self.index}

    def send(self, conn, kind, obj):
        data = json.dumps(obj).encode()
        conn.sendall(HEADER.pack(b"i3-ipc", len(data), kind) + data)

    def serve(self):
        while True:
            conn, _ = self.server.accept()
            threading.Thread(target=self.client, args=(conn,), daemon=True).start()

    def client(self, conn):
        try:
            while True:
                head = conn.recv(HEADER.size)
                if len(head) < HEADER.size:
                    return
                _, length, kind = HEADER.unpack(head)
                payload = conn.recv(length).decode() if length else ""
                if kind == 100:
                    self.send(conn, 100, [{"type": "pointer"}, self.keyboard()])
                elif kind == 0:
                    self.index = int(payload.rsplit(" ", 1)[1])
                    self.send(conn, 0, [{"success": True}])
                    self.event(0x80000015, {"change": "xkb_layout", "input": self.keyboard()})
                elif kind == 4:
                    self.send(conn, 4, {"type": "root", "nodes": [{"type": "con", "focused": True, "app_id": "foot",
                                                                   "pid": 42, "fullscreen_mode": 0, "nodes": []}]})
                elif kind == 2:
                    self.send(conn, 2, {"success": True})
                    self.subscribers.append(conn)
        except OSError:
            pass

    def event(self, kind, obj):
        for conn in self.subscribers:
            self.send(conn, kind, obj)


fake = FakeSway(str(tmp / "sway.sock"))
os.environ["SWAYSOCK"] = str(tmp / "sway.sock")
from humantype.linux.desktops.sway import Sway  # noqa: E402

calls = Calls()
sway = Sway(*calls.args())
pump(1.0, until=lambda: sway.present)
check("sway: connected", sway.present, True)
check("sway: layout", sway.layout(), "en")
check("sway: focused window from the tree", (sway.app, sway.pid), ("foot", 42))
check("sway: switch to ru", sway.set_layout("ru"), True)
check("sway: compositor index", fake.index, 1)
fake.event(0x80000003, {"change": "focus", "container": {"app_id": None, "pid": 7, "fullscreen_mode": 1,
                                                         "window_properties": {"class": "Steam"}}})
pump(0.3, until=lambda: sway.wm_class == "Steam")
check("sway: window event", (sway.wm_class, sway.fullscreen, sway.pid), ("Steam", True, 7))
fake.index = 0
fake.event(0x80000015, {"change": "xkb_layout", "input": fake.keyboard()})
pump(0.3, until=lambda: calls.layouts[-1:] == ["en"])
check("sway: layout event", calls.layouts[-1], "en")
check("sway: no German", sway.set_layout("de"), False)

# --- Hyprland ---

hdir = tmp / "hypr" / "sig"
hdir.mkdir(parents=True)


class FakeHypr:
    def __init__(self):
        self.index = 0
        self.events = []
        self.req = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.req.bind(str(hdir / ".socket.sock"))
        self.req.listen()
        self.ev = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ev.bind(str(hdir / ".socket2.sock"))
        self.ev.listen()
        threading.Thread(target=self.serve_requests, daemon=True).start()
        threading.Thread(target=self.serve_events, daemon=True).start()

    def serve_requests(self):
        while True:
            conn, _ = self.req.accept()
            cmd = conn.recv(4096).decode()
            if cmd == "j/getoption input:kb_layout":
                out = {"option": "input:kb_layout", "str": "us,ru"}
            elif cmd == "j/devices":
                out = {"keyboards": [{"name": "kb", "main": True, "active_keymap": ["English (US)", "Russian"][self.index],
                                      "active_layout_index": self.index}]}
            elif cmd == "j/activewindow":
                out = {"class": "firefox", "pid": 99, "fullscreen": 0}
            elif cmd.startswith("switchxkblayout all "):
                self.index = int(cmd.rsplit(" ", 1)[1])
                out = "ok"
                for c in self.events:
                    c.sendall(f"activelayout>>kb,{['English (US)', 'Russian'][self.index]}\n".encode())
            else:
                out = "unknown request"
            conn.sendall((out if isinstance(out, str) else json.dumps(out)).encode())
            conn.close()

    def serve_events(self):
        while True:
            conn, _ = self.ev.accept()
            self.events.append(conn)


hfake = FakeHypr()
os.environ["XDG_RUNTIME_DIR"] = str(tmp)
os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = "sig"
from humantype.linux.desktops.hyprland import Hyprland  # noqa: E402

calls = Calls()
hypr = Hyprland(*calls.args())
pump(1.0, until=lambda: hypr.present)
check("hyprland: connected", hypr.present, True)
check("hyprland: layout", hypr.layout(), "en")
check("hyprland: focused window", (hypr.wm_class, hypr.pid), ("firefox", 99))
check("hyprland: switch to ru", hypr.set_layout("ru"), True)
pump(0.3, until=lambda: calls.layouts[-1:] == ["ru"])
check("hyprland: layout event", calls.layouts[-1], "ru")
check("hyprland: index", hfake.index, 1)
for c in hfake.events:
    c.sendall(b"activelayout>>kb,English (US)\nactivewin")  # a line split across two reads
pump(0.2)
check("hyprland: line split across reads", calls.layouts[-1], "en")

# --- KDE ---


kde_proc = subprocess.Popen([sys.executable, __file__, "--fake-kde", str(tmp / "kde_index")])
pump(1.0)


def kde_index():
    try:
        return int((tmp / "kde_index").read_text())
    except (OSError, ValueError):
        return -1


from humantype.linux.desktops.kde import Kde  # noqa: E402
from humantype.linux.shell import DaemonService  # noqa: E402

history_calls = []


def on_history(method, args):
    history_calls.append((method, args))
    return ("text", "привет", "") if method == "HistoryGet" else None


calls = Calls()
kde = Kde(*calls.args())
service = DaemonService(kde.bus, lambda: True, lambda on: None, kde.window_changed, on_history)
pump(1.0, until=lambda: kde.present)
check("kde: connected", kde.present, True)
check("kde: layout", kde.layout(), "ru")
check("kde: switch to en", kde.set_layout("en"), True)
pump(0.2)
check("kde: keyboard daemon index", kde_index(), 0)
# What the KWin script does when a window gets the focus:
proc = Gio.DBusConnection.new_for_address_sync(Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
                                               Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                                               | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
proc.call("org.humantype.Daemon", "/org/humantype/Daemon", "org.humantype.Daemon", "WindowChanged",
          GLib.Variant("(ssbi)", ("org.kde.konsole", "org.kde.konsole.desktop", False, 5)), None,
          Gio.DBusCallFlags.NONE, 1000, None, None)
pump(0.5, until=lambda: kde.pid == 5)
check("kde: window from the KWin script", (kde.wm_class, kde.app, kde.pid), ("org.kde.konsole", "org.kde.konsole", 5))
check("kde: focus callback", calls.focus >= 1, True)
state = kde.state("ghbdtn")
check("kde: state", (state.layout, state.app, state.text_known), ("en", "org.kde.konsole", False))

# The clipboard history, as the GNOME extension calls it:
replies = []
for method, sig, args in (("ClipboardAdd", "(ss)", ("скопировано", "")), ("HistoryGet", "(u)", (7,)),
                          ("HistoryPin", "(u)", (7,)), ("HistoryDone", "(u)", (7,))):
    proc.call("org.humantype.Daemon", "/org/humantype/Daemon", "org.humantype.Daemon", method,
              GLib.Variant(sig, args), None, Gio.DBusCallFlags.NONE, 1000, None,
              lambda conn, res, m=method: replies.append((m, conn.call_finish(res).unpack())))
pump(1.0, until=lambda: len(replies) == 4)
check("history: calls reach the service", history_calls,
      [("ClipboardAdd", ("скопировано", "")), ("HistoryGet", (7,)), ("HistoryPin", (7,)), ("HistoryDone", (7,))])
check("history: an item comes back", dict(replies).get("HistoryGet"), ("text", "привет", ""))

kde_proc.terminate()
print(f"FAILS: {fails}")
sys.exit(1 if fails else 0)
