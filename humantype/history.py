"""Clipboard history: text and pictures, kept in SQLite with pictures as PNG files."""

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QGuiApplication, QImage

# Formats password managers put on the clipboard to ask history tools to skip it.
PRIVATE_MARKERS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
MAX_TEXT = 200_000


@dataclass
class Item:
    id: int
    kind: str  # "text" | "image"
    text: str  # the text itself, or "1920×1080" for a picture
    image: str | None  # PNG path for a picture
    pinned: bool
    created: float


class History(QObject):
    changed = Signal()

    def __init__(self, db_path: Path, images_dir: Path, limit: int = 100) -> None:
        super().__init__()
        self.images_dir = images_dir
        self.limit = limit
        self.save_images = True
        self.paused = False
        self._suppressed = 0
        self.db = sqlite3.connect(db_path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                image TEXT,
                hash TEXT UNIQUE NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL)"""
        )
        self.db.commit()
        QGuiApplication.clipboard().dataChanged.connect(self._on_clipboard)

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
        digest = hashlib.sha1(("t:" + text).encode("utf-8", "surrogatepass")).hexdigest()
        self._upsert("text", text, None, digest)

    def add_image(self, image: QImage) -> None:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        png = bytes(data.data())
        digest = hashlib.sha1(png).hexdigest()
        path = self.images_dir / f"{digest}.png"
        if not path.exists():
            path.write_bytes(png)
        self._upsert("image", f"{image.width()}×{image.height()}", str(path), digest)

    def _upsert(self, kind: str, text: str, image: str | None, digest: str) -> None:
        now = time.time()
        row = self.db.execute("SELECT id FROM items WHERE hash = ?", (digest,)).fetchone()
        if row:
            self.db.execute("UPDATE items SET created = ? WHERE id = ?", (now, row[0]))
        else:
            self.db.execute(
                "INSERT INTO items (kind, text, image, hash, created) VALUES (?, ?, ?, ?, ?)",
                (kind, text, image, digest, now),
            )
        self._trim()
        self.db.commit()
        self.changed.emit()

    def _trim(self) -> None:
        old = self.db.execute(
            "SELECT id, image FROM items WHERE pinned = 0 ORDER BY created DESC LIMIT -1 OFFSET ?",
            (self.limit,),
        ).fetchall()
        for item_id, image in old:
            self._delete_row(item_id, image)

    def _delete_row(self, item_id: int, image: str | None) -> None:
        self.db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if image:
            Path(image).unlink(missing_ok=True)

    # --- reading and editing ---

    def items(self, query: str = "") -> list[Item]:
        rows = self.db.execute(
            "SELECT id, kind, text, image, pinned, created FROM items ORDER BY pinned DESC, created DESC"
        ).fetchall()
        items = [Item(r[0], r[1], r[2], r[3], bool(r[4]), r[5]) for r in rows]
        q = query.strip().lower()
        if not q:
            return items
        return [i for i in items if q in i.text.lower() or (i.kind == "image" and q in "картинка изображение image")]

    def set_limit(self, limit: int) -> None:
        self.limit = limit
        self._trim()
        self.db.commit()
        self.changed.emit()

    def toggle_pin(self, item_id: int) -> None:
        self.db.execute("UPDATE items SET pinned = 1 - pinned WHERE id = ?", (item_id,))
        self.db.commit()
        self.changed.emit()

    def delete(self, item_id: int) -> None:
        row = self.db.execute("SELECT image FROM items WHERE id = ?", (item_id,)).fetchone()
        if row:
            self._delete_row(item_id, row[0])
            self.db.commit()
            self.changed.emit()

    def clear(self) -> None:
        """Removes everything except pinned items."""
        for item_id, image in self.db.execute("SELECT id, image FROM items WHERE pinned = 0").fetchall():
            self._delete_row(item_id, image)
        self.db.commit()
        self.changed.emit()
