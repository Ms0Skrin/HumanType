"""End to end on the real GNOME session: a virtual test keyboard types into a test window, through
the real service and the real extension. Your own keyboards are left alone (HUMANTYPE_ONLY), the
settings go to a temporary folder, and nothing is typed unless the test window has the focus.

Needs the extension loaded (linux/install.sh, then log in again). Do not type or click meanwhile.

    python tests/linux_e2e.py
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import evdev  # noqa: E402
import gi  # noqa: E402
from evdev import ecodes as e  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from humantype.linux.keymap import US_KEYS  # noqa: E402

APP_ID = "org.humantype.E2E"
TEST_NAME = "HumanType-test keyboard"
KEY_GAP = 0.012

CHAR = {}
for code, (plain, shifted) in US_KEYS.items():
    CHAR.setdefault(plain, (code, False))
    CHAR.setdefault(shifted, (code, True))
CHAR[" "] = (e.KEY_SPACE, False)
CHAR["\n"] = (e.KEY_ENTER, False)
CHAR["\b"] = (e.KEY_BACKSPACE, False)
PAUSE, SHIFT_PAUSE = "\x01", "\x02"

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def shell(method, params, reply):
    result = bus.call_sync("org.humantype.Shell", "/org/humantype/Shell", "org.humantype.Shell", method, params,
                           GLib.VariantType(reply), Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
    return result.unpack()


def state():
    return shell("GetState", GLib.Variant("(sb)", ("", False)), "(a{sv})")[0]


def set_layout(lang):
    if not shell("SetLayout", GLib.Variant("(s)", (lang,)), "(b)")[0]:
        raise SystemExit(f"cannot switch to {lang}")
    time.sleep(0.15)


class Run:
    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="humantype-e2e-"))
        self.state_file = str(self.tmp / "state.json")
        self.command_file = str(self.tmp / "command")
        self.log_file = self.tmp / "daemon.log"
        self.fails = 0
        self.kbd = None
        self.target = None
        self.daemon = None

    # --- setup ---

    def start(self) -> None:
        try:
            state()
        except GLib.Error as error:
            raise SystemExit(f"the extension does not answer ({error.message}): log out and in again") from None
        self.original_layout = state().get("layout") or "en"
        # The whole main block (Esc to F12): only then udev calls it a keyboard and gives us access to it.
        keys = sorted(set(range(e.KEY_ESC, e.KEY_F12 + 1)) | {e.KEY_PAUSE})
        self.kbd = evdev.UInput({e.EV_KEY: keys, e.EV_LED: [e.LED_CAPSL, e.LED_NUML]}, name=TEST_NAME)
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1")
        self.target = subprocess.Popen([sys.executable, str(ROOT / "tests" / "linux_e2e_target.py"),
                                        self.state_file, self.command_file], env=env)
        env.update(HUMANTYPE_ONLY="HumanType-test", XDG_CONFIG_HOME=str(self.tmp / "config"))
        self.daemon = subprocess.Popen([sys.executable, "-m", "humantype.linux"], env=env,
                                       stdout=self.log_file.open("w"), stderr=subprocess.STDOUT)
        self.wait_for(lambda: TEST_NAME in self.log_file.read_text(), 10, "the service did not take the test keyboard")
        self.wait_focus(20)

    def wait_for(self, cond, timeout, message) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.daemon and self.daemon.poll() is not None:
                raise SystemExit(f"the service exited:\n{self.log_file.read_text()}")
            try:
                if cond():
                    return
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(0.05)
        raise SystemExit(message)

    def focused(self) -> bool:
        return state().get("wm_class") == APP_ID

    def wait_focus(self, timeout) -> None:
        if not self.focused():
            print("Кликни по окну «HumanType test», чтобы дать ему фокус…", flush=True)
        self.wait_for(self.focused, timeout, "the test window has no focus: nothing typed")

    def stop(self) -> None:
        if self.daemon and self.daemon.poll() is None:
            self.daemon.send_signal(signal.SIGTERM)
            try:
                self.daemon.wait(5)
            except subprocess.TimeoutExpired:
                self.daemon.kill()
        if self.target and self.target.poll() is None:
            self.target.terminate()
        if self.kbd:
            self.kbd.close()
        try:
            set_layout(self.original_layout)
        except (GLib.Error, SystemExit):
            print(f"could not switch the layout back to {self.original_layout}")

    # --- typing ---

    def command(self, command) -> None:
        done = self.command_file + ".done"
        if os.path.exists(done):
            os.remove(done)
        Path(self.command_file).write_text(command)
        self.wait_for(lambda: os.path.exists(done), 3, f"the window did not do {command!r}")

    def key(self, code, value) -> None:
        self.kbd.write(e.EV_KEY, code, value)
        self.kbd.syn()
        time.sleep(KEY_GAP)

    def type_(self, text) -> None:
        for ch in text:
            if not self.focused():
                raise SystemExit("the focus left the test window: stopped typing")
            if ch in (PAUSE, SHIFT_PAUSE):
                if ch == SHIFT_PAUSE:
                    self.key(e.KEY_LEFTSHIFT, 1)
                self.key(e.KEY_PAUSE, 1)
                self.key(e.KEY_PAUSE, 0)
                if ch == SHIFT_PAUSE:
                    self.key(e.KEY_LEFTSHIFT, 0)
                time.sleep(0.2)
                continue
            code, shift = CHAR[ch]
            if shift:
                self.key(e.KEY_LEFTSHIFT, 1)
            self.key(code, 1)
            self.key(code, 0)
            if shift:
                self.key(e.KEY_LEFTSHIFT, 0)
            if ch in " \n\b":
                time.sleep(0.15)  # a fix happens here: let it finish before the next key

    def read(self, field) -> str:
        """The field once it stops changing."""
        last, since = None, time.monotonic()
        while time.monotonic() - since < 0.4:
            now = json.loads(Path(self.state_file).read_text())[field]
            if now != last:
                last, since = now, time.monotonic()
            time.sleep(0.03)
        return last

    def case(self, name, layout, keys, want, field="text") -> None:
        self.command("clear")
        self.command("focus password" if field == "password" else "focus text")
        set_layout(layout)
        self.wait_focus(5)
        t0 = time.monotonic()
        self.type_(keys)
        got = self.read(field)
        ok = got == want
        self.fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (want {want!r})")
              + f"  [{time.monotonic() - t0:.1f} s]", flush=True)


def main() -> int:
    run = Run()
    try:
        run.start()
        run.case("en -> ru", "en", "ghbdtn ", "привет ")
        run.case("english untouched", "en", "hello world ", "hello world ")
        run.case("ru -> en", "ru", "hello world ", "hello world ")
        run.case("several words", "en", "vfvf vskf hfve ", "мама мыла раму ")
        run.case("capital letter", "en", "Ghbdtn ", "Привет ")
        run.case("short words", "en", "z nt,z k.,k. ", "я тебя люблю ")
        run.case("Enter", "en", "ghbdtn\n", "привет\n")
        run.case("undo with Backspace", "en", "ntcnjdsq \b", "ntcnjdsq")
        run.case("Pause flips the word", "en", "ghbdtn" + PAUSE, "привет")
        run.case("Shift+Pause flips the line", "en", "hello world" + SHIFT_PAUSE, "руддщ цщкдв")
        run.case("typing on after a fix", "en", "ghbdtn vbh ", "привет мир ")
        run.case("password field untouched", "en", "ghbdtn ", "ghbdtn ", field="password")
        layout = state().get("layout")
        print(f"layout now: {layout}")
    finally:
        run.stop()
        log = run.log_file.read_text() if run.log_file.exists() else ""
        bad = [line for line in log.splitlines() if line.startswith(("ERROR", "CRITICAL", "Traceback"))]
        if bad:
            print("service errors:\n  " + "\n  ".join(bad))
            run.fails += 1
        print(f"service log: {run.log_file}")
    print(f"FAILS: {run.fails}")
    return 1 if run.fails else 0


if __name__ == "__main__":
    sys.exit(main())
