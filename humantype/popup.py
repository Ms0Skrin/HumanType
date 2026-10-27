"""The clipboard history window that opens at the cursor."""

import time

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import theme, winapi
from .history import History, Item
from .icons import line_icon

SHADOW = 18  # room around the card for its shadow


def age(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:
        return "только что"
    if s < 3600:
        return f"{s // 60} мин назад"
    if s < 86400:
        return f"{s // 3600} ч назад"
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def preview(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def corner_at(pos: QPoint, size: QSize, inset: int = 0) -> QPoint:
    """Where to put a window so its visible bottom-right corner sits at `pos`, kept on the screen
    above the taskbar. `inset` is the transparent margin around the visible part (a shadow)."""
    screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    w, h = size.width() - 2 * inset, size.height() - 2 * inset
    right = min(max(pos.x(), area.left() + w), area.right() + 1)
    bottom = min(max(pos.y(), area.top() + h), area.bottom() + 1)
    return QPoint(right - w - inset, bottom - h - inset)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


class ItemDelegate(QStyledItemDelegate):
    """Draws one history entry: a rounded tile with the text (or a thumbnail) and a muted line under it."""

    THUMB_W, THUMB_H = 72, 44

    def __init__(self, popup: "ClipPopup") -> None:
        super().__init__(popup)
        self.popup = popup
        self.pin = line_icon("pin", theme.ACCENT)

    def sizeHint(self, option, index) -> QSize:
        item = index.data(Qt.ItemDataRole.UserRole)
        return QSize(option.rect.width(), 62 if item and item.kind == "image" else 54)

    def paint(self, p: QPainter, option, index) -> None:
        item: Item | None = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(option.rect).adjusted(1, 2, -1, -2)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if item is None:  # the "empty" line
            p.setPen(QColor(theme.MUTED))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, index.data())
            p.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            p.setPen(QPen(QColor(56, 189, 248, 115), 1))
            p.setBrush(QColor(56, 189, 248, 36))
        elif hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.SURFACE_2))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 10, 10)

        x = rect.left() + 12
        if item.kind == "image":
            thumb = QRectF(x, rect.center().y() - self.THUMB_H / 2, self.THUMB_W, self.THUMB_H)
            pixmap = self.popup.thumb(item.image)
            path = QPainterPath()
            path.addRoundedRect(thumb, 6, 6)
            p.setClipPath(path)
            p.fillRect(thumb, QColor(theme.BG))
            if not pixmap.isNull():
                scaled = pixmap.scaled(thumb.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap(
                    int(thumb.center().x() - scaled.width() / 2), int(thumb.center().y() - scaled.height() / 2), scaled
                )
            p.setClipping(False)
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(thumb, 6, 6)
            x = thumb.right() + 12
            title = f"Картинка  {item.text}"
            meta = age(item.created)
        else:
            title = preview(item.text)
            meta = f"{age(item.created)}   ·   {plural(len(item.text), 'символ', 'символа', 'символов')}"

        right = rect.right() - 12
        if item.pinned:
            self.pin.paint(p, int(right - 16), int(rect.top() + 10), 16, 16)
            right -= 22

        title_font = QFont(option.font)
        title_font.setPointSizeF(10)
        p.setFont(title_font)
        p.setPen(QColor("#FFFFFF" if selected else theme.TEXT))
        title_rect = QRectF(x, rect.top() + 8, right - x, 20)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, int(title_rect.width())))

        meta_font = QFont(option.font)
        meta_font.setPointSizeF(8.5)
        p.setFont(meta_font)
        p.setPen(QColor(theme.ACCENT if selected else theme.MUTED))
        p.drawText(QRectF(x, rect.bottom() - 24, right - x, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, meta)
        p.restore()


class ClipPopup(QWidget):
    def __init__(self, history: History, notify) -> None:
        super().__init__()
        self.history = history
        self.notify = notify
        self.target_hwnd: int | None = None
        self._thumbs: dict[str, QPixmap] = {}

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(theme.POPUP_QSS)
        self.resize(480 + 2 * SHADOW, 540 + 2 * SHADOW)

        card = QFrame()
        card.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)

        icon = QLabel()
        icon.setPixmap(line_icon("clipboard", theme.ACCENT).pixmap(18, 18))
        title = QLabel("История буфера")
        title.setObjectName("title")
        self.count = QLabel()
        self.count.setObjectName("count")
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(icon)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.count)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск…  (слово «картинка» покажет изображения)")
        self.search.textChanged.connect(self.refresh)
        self.search.installEventFilter(self)
        self.list = QListWidget()
        self.list.setItemDelegate(ItemDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(False)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemActivated.connect(lambda _: self.paste_current())
        hint = QLabel("↵ вставить   ·   Ctrl+P закрепить   ·   Del удалить   ·   Esc закрыть")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 14, 16, 12)
        inner.setSpacing(10)
        inner.addLayout(header)
        inner.addWidget(self.search)
        inner.addWidget(self.list, 1)
        inner.addWidget(hint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        outer.addWidget(card)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(130)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        history.changed.connect(lambda: self.isVisible() and self.refresh())

    # --- showing ---

    def open_at_cursor(self, target_hwnd: int | None) -> None:
        """target_hwnd is the window to paste into; None only copies (opened from the tray),
        and then the window opens like the tray menu: its bottom-right corner at the click."""
        self.target_hwnd = target_hwnd
        self.search.clear()
        self.refresh()
        pos = QCursor.pos()
        if target_hwnd is None:
            self.move(corner_at(pos, self.size(), SHADOW))
        else:
            screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            x = min(max(pos.x() - 60, area.left()), area.right() - self.width())
            y = min(max(pos.y() - 6, area.top()), area.bottom() - self.height())
            self.move(x, y)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        winapi.focus(int(self.winId()))
        self.search.setFocus()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def refresh(self) -> None:
        current = self.list.currentItem()
        current_item = current.data(Qt.ItemDataRole.UserRole) if current else None
        current_id = current_item.id if current_item else None
        self.list.clear()
        items = self.history.items(self.search.text())
        total = len(self.history.items())
        self.count.setText(
            f"{len(items)} из {total}" if self.search.text() else plural(total, "запись", "записи", "записей")
        )
        for item in items:
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.UserRole, item)
            if item.kind == "text":
                row.setToolTip(item.text[:1500])
            self.list.addItem(row)
            if item.id == current_id:
                self.list.setCurrentItem(row)
        if self.list.currentRow() < 0 and items:
            self.list.setCurrentRow(0)
        if not items:
            empty = QListWidgetItem(
                "Ничего не найдено." if self.search.text() else "Пусто. Скопируй текст или картинку — они появятся здесь."
            )
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list.addItem(empty)

    def thumb(self, path: str | None) -> QPixmap:
        if not path:
            return QPixmap()
        if path not in self._thumbs:
            self._thumbs[path] = QPixmap(path).scaled(
                160, 96, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
        return self._thumbs[path]

    # --- acting ---

    def _current(self) -> Item | None:
        row = self.list.currentItem()
        return row.data(Qt.ItemDataRole.UserRole) if row else None

    def paste_current(self) -> None:
        item = self._current()
        if item is None:
            return
        clipboard = QGuiApplication.clipboard()
        if item.kind == "image":
            clipboard.setImage(QImage(item.image))
        else:
            clipboard.setText(item.text)
        self.hide()
        target = self.target_hwnd
        if target is None:
            self.notify("Скопировано в буфер")
            return

        def paste() -> None:
            winapi.focus(target)
            QTimer.singleShot(60, lambda: (winapi.release_modifiers(), winapi.combo(0x11, winapi.VK_V)))

        QTimer.singleShot(30, paste)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            return self._key(event)
        return super().eventFilter(obj, event)

    def _key(self, event: QKeyEvent) -> bool:
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.paste_current()
        elif key == Qt.Key.Key_Escape:
            self.hide()
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            QGuiApplication.sendEvent(self.list, QKeyEvent(event.type(), key, mods, event.text()))
        elif key == Qt.Key.Key_P and mods & Qt.KeyboardModifier.ControlModifier:
            if item := self._current():
                self.history.toggle_pin(item.id)
        elif key == Qt.Key.Key_Delete and not self.search.text():
            if item := self._current():
                row = self.list.currentRow()
                self.history.delete(item.id)
                self.list.setCurrentRow(min(row, self.list.count() - 1))
        else:
            return False
        return True

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)
