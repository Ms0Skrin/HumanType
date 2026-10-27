"""The window the end-to-end test types into: a text view and a password field.

It writes what they hold to the state file on every change, and takes commands from the command
file ("clear", "focus text", "focus password"), so the test needs no other way in.

    python tests/linux_e2e_target.py STATE_FILE COMMAND_FILE
"""

import json
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

APP_ID = "org.humantype.E2E"


class Target(Gtk.Application):
    def __init__(self, state: str, command: str) -> None:
        super().__init__(application_id=APP_ID)
        self.state_file = state
        self.command_file = command

    def do_activate(self) -> None:
        win = Gtk.ApplicationWindow(application=self, title="HumanType test - не трогай")
        win.set_default_size(700, 300)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        self.password = Gtk.PasswordEntry()
        self.text = Gtk.TextView(vexpand=True, wrap_mode=Gtk.WrapMode.WORD)
        box.append(self.password)
        box.append(self.text)
        win.set_child(box)
        self.text.get_buffer().connect("changed", lambda *_: self._dump())
        self.password.connect("changed", lambda *_: self._dump())
        win.present()
        self.text.grab_focus()
        self._dump()
        GLib.timeout_add(20, self._poll)

    def _dump(self) -> None:
        buf = self.text.get_buffer()
        data = {"text": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True),
                "password": self.password.get_text(),
                "focus": "password" if self.password.has_focus() or self.password.get_focus_child() else "text"}
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.state_file)

    def _poll(self) -> bool:
        try:
            with open(self.command_file, encoding="utf-8") as f:
                command = f.read().strip()
            os.remove(self.command_file)
        except FileNotFoundError:
            return GLib.SOURCE_CONTINUE
        if command == "clear":
            self.text.get_buffer().set_text("")
            self.password.set_text("")
        elif command == "focus text":
            self.text.grab_focus()
        elif command == "focus password":
            self.password.grab_focus()
        elif command == "quit":
            self.quit()
        self._dump()
        with open(self.command_file + ".done", "w") as f:
            f.write(command)
        return GLib.SOURCE_CONTINUE


if __name__ == "__main__":
    sys.exit(Target(sys.argv[1], sys.argv[2]).run([]))
