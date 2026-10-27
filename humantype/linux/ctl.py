"""The app menu entry: turns automatic switching on and off, starting the service if it is not running.

    python -m humantype.linux.ctl [toggle|on|off]
"""

import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .shell import DAEMON_NAME, DAEMON_PATH  # noqa: E402


def daemon() -> Gio.DBusProxy | None:
    proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                                           DAEMON_NAME, DAEMON_PATH, "org.humantype.Daemon", None)
    return proxy if proxy.get_name_owner() else None


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    proxy = daemon()
    if proxy is None:
        # Not running: starting it is what was asked for, whatever the action.
        subprocess.run(["systemctl", "--user", "start", "humantype.service"], check=False)
        for _ in range(30):
            time.sleep(0.1)
            proxy = daemon()
            if proxy is not None:
                break
        if proxy is None:
            print("the humantype service does not start: journalctl --user -u humantype", file=sys.stderr)
            return 1
        if action == "toggle":
            action = "on"
    enabled = proxy.get_cached_property("Enabled")
    on = {"on": True, "off": False}.get(action, not (enabled.unpack() if enabled else False))
    proxy.call_sync("SetEnabled", GLib.Variant("(b)", (on,)), Gio.DBusCallFlags.NONE, 2000, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
