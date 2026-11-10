"""How much HumanType has fixed: counts per day, and the words whose fix was undone most often
(they are candidates for the exceptions). Counts and those words only: nothing else that was typed."""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

KINDS = ("fixed", "manual", "undone")
KEEP_DAYS = 120
KEEP_WORDS = 200


def _day(offset: int = 0) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - offset * 86400))


class Stats:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.days: dict[str, dict[str, int]] = {}
        self.total: dict[str, int] = {kind: 0 for kind in KINDS}
        self.undone: dict[str, int] = {}  # key spelling of a word -> how many times its fix was undone
        self.dirty = False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.days = {str(day): {k: int(v) for k, v in counts.items() if k in KINDS} for day, counts in raw.get("days", {}).items()}
            self.total.update({k: int(v) for k, v in raw.get("total", {}).items() if k in KINDS})
            self.undone = {str(word): int(n) for word, n in raw.get("undone", {}).items()}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("stats.json is unreadable, starting anew")

    def add(self, kind: str, word: str = "") -> None:
        """One more fix ("fixed": by itself, "manual": by the hotkey) or one more undo, of `word`."""
        today = self.days.setdefault(_day(), {})
        today[kind] = today.get(kind, 0) + 1
        self.total[kind] = self.total.get(kind, 0) + 1
        if word:
            self.undone[word] = self.undone.get(word, 0) + 1
        self.dirty = True

    def count(self, kind: str, days: int | None = None) -> int:
        """The count for the last `days` days, today included; for all time when None."""
        if days is None:
            return self.total.get(kind, 0)
        return sum(self.days.get(_day(i), {}).get(kind, 0) for i in range(days))

    def last_days(self, days: int, kind: str = "fixed") -> list[tuple[str, int]]:
        """(day, count), the oldest first."""
        return [(_day(i), self.days.get(_day(i), {}).get(kind, 0)) for i in range(days - 1, -1, -1)]

    def top_undone(self, limit: int = 20) -> list[tuple[str, int]]:
        return sorted(self.undone.items(), key=lambda item: (-item[1], item[0]))[:limit]

    def clear(self) -> None:
        self.days, self.undone = {}, {}
        self.total = {kind: 0 for kind in KINDS}
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        days = dict(sorted(self.days.items())[-KEEP_DAYS:])
        undone = dict(self.top_undone(KEEP_WORDS))
        self.days, self.undone = days, undone
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": days, "total": self.total, "undone": undone}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            self.dirty = False
        except OSError:
            log.exception("stats.json not saved")
