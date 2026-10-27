"""Windows only, on a desktop: a Qt window with a text field; humantype.uia must read what is before its caret.
No keys are pressed: the text and the caret are set by the program."""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pythoncom  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget  # noqa: E402

from humantype import uia  # noqa: E402

app = QApplication([])
window = QWidget()
line = QLineEdit()
area = QPlainTextEdit()
layout = QVBoxLayout(window)
layout.addWidget(line)
layout.addWidget(area)
window.show()
window.raise_()
window.activateWindow()

fails = 0
results = []


def ask(limit):
    out = []

    def run():
        pythoncom.CoInitialize()
        out.append(uia.text_before_caret(limit))

    t = threading.Thread(target=run)
    t.start()
    while t.is_alive():
        app.processEvents()
        t.join(0.01)
    return out[0]


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("OK  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


def steps():
    line.setFocus()
    line.setText("abc ghbdtn")
    line.setCursorPosition(len(line.text()))
    app.processEvents()
    check("line end", ask(7), " ghbdtn")
    line.setCursorPosition(6)
    check("line middle", ask(4), "c gh")
    check("line short", ask(50), "abc gh")
    area.setFocus()
    area.setPlainText("первая\nвторая строка")
    cursor = area.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    area.setTextCursor(cursor)
    app.processEvents()
    check("multiline", ask(14), "\nвторая строка")
    print("FAILS:", fails)
    app.exit(1 if fails else 0)


QTimer.singleShot(500, steps)
sys.exit(app.exec())
