"""Clipboard history: text and pictures, kept in SQLite with pictures as PNG files (humantype.clipstore)."""

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QGuiApplication, QImage

from .clipstore import MAX_TEXT, ClipStore, Item  # noqa: F401  (Item is used by the popup)

# Formats password managers put on the clipboard to ask history tools to skip it.
PRIVATE_MARKERS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")


class History(QObject):
    changed = Signal()

    def __init__(self, db_path: Path, images_dir: Path, limit: int = 100) -> None:
        super().__init__()
        self.store = ClipStore(db_path, images_dir, limit)
        self.save_images = True
        self.paused = False
        self._suppressed = 0
        QGuiApplication.clipboard().dataChanged.connect(self._on_clipboard)

    @property
    def limit(self) -> int:
        return self.store.limit

    # --- capture ---

    def suppress(self, on: bool) -> None:
        """Ignore clipboard changes the app makes itself (the layout fixer's copy and paste)."""
        self._suppressed = max(0, self._suppressed + (1 if on else -1))

    def _on_clipboard(self) -> None:
        if self.paused or self._suppressed:
            return
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None:
            return
        formats = " ".join(mime.formats())
        if any(marker in formats for marker in PRIVATE_MARKERS):
            return
        # Word and Excel put a picture beside copied text: the text is what was meant.
        if mime.hasText() and mime.text().strip():
            self.add_text(mime.text()[:MAX_TEXT])
        elif mime.hasImage() and self.save_images:
            image = clipboard.image()
            if not image.isNull():
                self.add_image(image)

    def add_text(self, text: str) -> None:
        self.store.add_text(text)
        self.changed.emit()

    def add_image(self, image: QImage) -> None:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.store.add_png(bytes(data.data()), f"{image.width()}×{image.height()}")
        self.changed.emit()

    # --- reading and editing ---

    def items(self, query: str = "") -> list[Item]:
        return self.store.items(query)

    def set_limit(self, limit: int) -> None:
        self.store.set_limit(limit)
        self.changed.emit()

    def toggle_pin(self, item_id: int) -> None:
        self.store.toggle_pin(item_id)
        self.changed.emit()

    def delete(self, item_id: int) -> None:
        self.store.delete(item_id)
        self.changed.emit()

    def clear(self) -> None:
        """Removes everything except pinned items."""
        self.store.clear()
        self.changed.emit()
