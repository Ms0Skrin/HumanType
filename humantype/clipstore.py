"""Clipboard history storage: text and pictures in SQLite, pictures as PNG files beside it.
No GUI here: the Windows app wraps it in Qt, the Linux service feeds it from the GNOME Shell extension."""

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

MAX_TEXT = 200_000


@dataclass
class Item:
    id: int
    kind: str  # "text" | "image"
    text: str  # the text itself, or "1920×1080" for a picture
    image: str | None  # PNG path for a picture
    pinned: bool
    created: float


class ClipStore:
    def __init__(self, db_path: Path, images_dir: Path, limit: int = 100) -> None:
        self.images_dir = images_dir
        self.limit = limit
        db_path.parent.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)
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

    # --- adding ---

    def add_text(self, text: str) -> None:
        text = text[:MAX_TEXT]
        digest = hashlib.sha1(("t:" + text).encode("utf-8", "surrogatepass")).hexdigest()
        self._upsert("text", text, None, digest)

    def add_png(self, png: bytes, size: str) -> None:
        """A picture as PNG bytes; `size` is how it is shown, "1920×1080"."""
        digest = hashlib.sha1(png).hexdigest()
        path = self.images_dir / f"{digest}.png"
        if not path.exists():
            path.write_bytes(png)
        self._upsert("image", size, str(path), digest)

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

    def toggle_pin(self, item_id: int) -> None:
        self.db.execute("UPDATE items SET pinned = 1 - pinned WHERE id = ?", (item_id,))
        self.db.commit()

    def delete(self, item_id: int) -> None:
        row = self.db.execute("SELECT image FROM items WHERE id = ?", (item_id,)).fetchone()
        if row:
            self._delete_row(item_id, row[0])
            self.db.commit()

    def clear(self) -> None:
        """Removes everything except pinned items."""
        for item_id, image in self.db.execute("SELECT id, image FROM items WHERE pinned = 0").fetchall():
            self._delete_row(item_id, image)
        self.db.commit()
