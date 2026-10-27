"""A small note near the mouse cursor that fades by itself and never takes focus."""

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QLabel

from . import theme


class Toast(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(theme.TOAST_QSS)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_faded)

    def show_text(self, text: str, ms: int = 1400) -> None:
        self._fade.stop()
        self.setText(text)
        self.adjustSize()
        pos = QCursor.pos() + QPoint(16, 20)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        pos.setX(min(pos.x(), area.right() - self.width()))
        pos.setY(min(pos.y(), area.bottom() - self.height()))
        self.move(pos)
        if not self.isVisible():
            self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._animate(1.0, 140)
        self._timer.start(ms)

    def _fade_out(self) -> None:
        self._animate(0.0, 260)

    def _animate(self, to: float, ms: int) -> None:
        self._fade.stop()
        self._fade.setDuration(ms)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(to)
        self._fade.start()

    def _on_faded(self) -> None:
        if self.windowOpacity() < 0.05:
            self.hide()
