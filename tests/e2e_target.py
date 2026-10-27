"""A plain text window for the end-to-end test: mirrors its text into a file as it changes."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit

out = Path(sys.argv[1])
app = QApplication(sys.argv)
edit = QPlainTextEdit()
edit.setWindowTitle("HumanType E2E target")
edit.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
edit.resize(700, 300)
edit.textChanged.connect(lambda: out.write_text(edit.toPlainText(), encoding="utf-8"))
edit.show()
edit.activateWindow()
app.exec()
