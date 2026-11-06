"""«Почему не исправилось?»: the last words automatic switching judged, and what it decided.

The journal lives in memory only (it holds what was typed) and is gone when the app quits."""

import html
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from . import theme
from .autoswitch import AutoSwitcher
from .icons import app_icon
from .paths import APP_NAME


class JournalWindow(QDialog):
    def __init__(self, autoswitcher: AutoSwitcher) -> None:
        super().__init__()
        self.autoswitcher = autoswitcher
        self.setWindowTitle(f"{APP_NAME} — почему не исправилось")
        self.setWindowIcon(app_icon())
        self.resize(620, 520)
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.BG}; }}
            QLabel {{ color: {theme.MUTED}; font-family: {theme.FONT}; font-size: 12px; }}
            QTextBrowser {{ background: {theme.SURFACE}; color: {theme.TEXT}; border: 1px solid {theme.BORDER};
                           border-radius: 10px; padding: 8px; font-family: {theme.FONT}; font-size: 13px; }}
            QPushButton {{ background: {theme.SURFACE_2}; color: {theme.TEXT}; border: 1px solid {theme.BORDER};
                          border-radius: 8px; padding: 6px 16px; font-family: {theme.FONT}; }}
            QPushButton:hover {{ background: {theme.SURFACE_3}; }}
        """)
        hint = QLabel(
            "Последние слова, которые HumanType разобрал, — новые сверху. Хранится только в памяти, "
            "в файл не пишется. Не исправил то, что надо, — нажмите Pause: слово исправится, "
            "и дальше HumanType будет исправлять его сам."
        )
        hint.setWordWrap(True)
        self.view = QTextBrowser()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.refresh)
        clear = QPushButton("Очистить")
        clear.clicked.connect(self._clear)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.close)
        for button in (refresh, clear, close):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addWidget(refresh)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(hint)
        layout.addWidget(self.view, 1)
        layout.addLayout(buttons)
        self.refresh()

    def showEvent(self, event) -> None:
        theme.style_window(self)
        super().showEvent(event)

    def refresh(self) -> None:
        notes = list(self.autoswitcher.journal)[::-1]
        if not notes:
            self.view.setHtml(f'<p style="color:{theme.MUTED}">Пока пусто: наберите что-нибудь и загляните снова.</p>')
            return
        rows = []
        for note in notes:
            color = theme.ACCENT if note.fixed else theme.MUTED
            mark = "✓" if note.fixed else "•"
            word = f"<b>{html.escape(note.word)}</b> — " if note.word else ""
            rows.append(
                f'<p style="margin:3px 0"><span style="color:{theme.MUTED}">{time.strftime("%H:%M:%S", time.localtime(note.when))}</span>'
                f'&nbsp; <span style="color:{color}">{mark}</span>&nbsp; {word}'
                f'<span style="color:{color}">{html.escape(note.why)}</span></p>'
            )
        self.view.setHtml("".join(rows))

    def _clear(self) -> None:
        self.autoswitcher.journal.clear()
        self.refresh()
