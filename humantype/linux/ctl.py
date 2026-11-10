"""The app menu entry: turns automatic switching on and off, starting the service if it is not running;
`update` installs the newest release (linux/install.sh --release) and says how it went;
`export-words FILE` and `import-words FILE` carry the exceptions to and from another computer
(the file is the one the Windows settings save and load).

    python -m humantype.linux.ctl [toggle|on|off|update|export-words FILE|import-words FILE]
"""

import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .shell import DAEMON_NAME, DAEMON_PATH  # noqa: E402


def daemon() -> Gio.DBusProxy | None:
    proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                                           DAEMON_NAME, DAEMON_PATH, "org.humantype.Daemon", None)
    return proxy if proxy.get_name_owner() else None


def notify(text: str) -> None:
    """A desktop notification (any desktop with org.freedesktop.Notifications); also printed."""
    print(text)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                      "Notify", GLib.Variant("(susssasa{sv}i)", ("HumanType", 0, "humantype", "HumanType", text, [], {}, 8000)),
                      None, Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        pass


def update() -> int:
    from .. import __version__, updater

    app_dir = Path(__file__).resolve().parents[2]
    try:
        release = updater.newer()
    except Exception as error:
        notify(f"Не удалось проверить обновления: {error}")
        return 1
    if release is None:
        notify(f"Установлена последняя версия ({__version__})")
        return 0
    if not (app_dir / ".git").exists():
        notify(f"Вышла версия {release.version}: {updater.PAGE}")
        return 1
    notify(f"Обновляю до {release.version}…")
    log = Path.home() / ".cache" / "humantype-update.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as out:
        # No terminal: install.sh skips the sudo step and says so instead of waiting for a password.
        done = subprocess.run([str(app_dir / "linux" / "install.sh"), "--release", release.tag],
                              stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    if done.returncode == 0:
        notify(f"HumanType обновлён до {release.version}")
        return 0
    notify(f"Обновление не удалось, подробности: {log}")
    return 1


def words(action: str, path: str) -> int:
    """The exceptions as a file for another computer, or taken from one. Linux keeps no learned
    words yet: those in a file from Windows are left out, and none are written."""
    from .. import wordlists
    from . import config

    if action == "export-words":
        Path(path).write_text(wordlists.dump(config.load_exceptions(), {}), encoding="utf-8")
        print(f"saved to {path}")
        return 0
    try:
        exceptions, learned = wordlists.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"cannot read {path}: {error}", file=sys.stderr)
        return 1
    have = config.load_exceptions()
    config.save_exceptions(have | exceptions)  # the service sees the file change and reads it again
    print(f"exceptions added: {len(exceptions - have)}" + (f"; learned words skipped: {len(learned)}" if learned else ""))
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    if action == "update":
        return update()
    if action in ("export-words", "import-words"):
        if len(sys.argv) < 3:
            print(f"usage: python -m humantype.linux.ctl {action} FILE", file=sys.stderr)
            return 2
        return words(action, sys.argv[2])
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
