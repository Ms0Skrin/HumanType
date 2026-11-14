"""The words HumanType was taught, as one file to carry between computers, Windows and Linux alike:
the exceptions (never switched) and the learned words (always switched).

Words are kept as the keys that type them, spelled as the US layout prints them (`ghbdtn`), so the
file means the same on any system."""

import json

from .layout import LANGS


def dump(exceptions: set[str], learned: dict[str, str]) -> str:
    return json.dumps(
        {"app": "HumanType", "format": 1, "exceptions": sorted(exceptions), "learned": dict(sorted(learned.items()))},
        ensure_ascii=False, indent=2,
    )


def parse(text: str) -> tuple[set[str], dict[str, str]]:
    """The exceptions and the learned words of a file made by `dump`. A plain list is read too:
    a word on a line is an exception, a word and `ru`, `en` or `uk` after it is a learned one
    (the form of exceptions.txt and autoswitch_learned.txt). ValueError when it is neither."""
    text = text.lstrip("﻿").strip()
    exceptions: set[str] = set()
    learned: dict[str, str] = {}
    if text.startswith("{"):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"не похоже на файл HumanType: {error}") from error
        if not isinstance(raw, dict) or not isinstance(raw.get("exceptions", []), list) or not isinstance(raw.get("learned", {}), dict):
            raise ValueError("не похоже на файл HumanType")
        exceptions = {str(word).lower() for word in raw.get("exceptions", []) if _is_word(str(word))}
        learned = {str(k).lower(): v for k, v in raw.get("learned", {}).items() if _is_word(str(k)) and v in LANGS}
        return exceptions, learned
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 1 and _is_word(parts[0]):
            exceptions.add(parts[0].lower())
        elif len(parts) == 2 and parts[1] in LANGS and _is_word(parts[0]):
            learned[parts[0].lower()] = parts[1]
        elif parts:
            raise ValueError(f"непонятная строка: {line[:40]}")
    return exceptions, learned


def _is_word(word: str) -> bool:
    return 0 < len(word) <= 64 and not any(c.isspace() for c in word)
