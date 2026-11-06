"""Runs anywhere: how often the detector fixes a line typed in the wrong layout, and how often it
touches a line typed right. Each line is typed as a person would: the same keys throughout, the
layout flipping only when a fix switches it.
    python tests/corpus_eval.py [-v]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from humantype.detector import Detector, Session, keys_of  # noqa: E402
from humantype.layout import EN_TO_RU  # noqa: E402

VERBOSE = "-v" in sys.argv


def type_line(session: Session, keys: str, layout: str) -> tuple[str, str]:
    """Types the key spellings word by word; returns the screen text and the final layout."""
    screen = ""
    session.reset()
    for word in keys.split(" "):
        if not word:
            screen += " "
            continue
        shown = word if layout == "en" else word.translate(EN_TO_RU)
        screen += shown
        fix = session.word(word, layout)
        if fix:
            assert screen.endswith(fix.original), (screen, fix.original)
            screen = screen[: len(screen) - fix.erase] + fix.text
            layout = fix.layout
            # the keys after the fix land in the new layout
        screen += " "
    return screen.rstrip(" "), layout


def lang_of(line: str) -> str:
    ru = sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in line)
    en = sum("a" <= c.lower() <= "z" for c in line)
    return "ru" if ru > en else "en"


def main() -> int:
    det = Detector(ROOT / "assets" / "dict")
    session = Session(det)
    wrong_ok = wrong_total = right_ok = right_total = 0
    for name in ("chat_corpus.txt", "hard_corpus.txt"):
        for line in (ROOT / "tests" / name).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            lang = lang_of(line)
            mixed = any("а" <= c.lower() <= "я" for c in line) and any("a" <= c.lower() <= "z" for c in line)
            # typed right: must stay as it is (a mixed line: the person switches by hand for each word)
            right_total += 1
            if mixed:
                session.reset()
                words = []
                for word in line.split(" "):
                    wl = lang_of(word) if any(c.isalpha() for c in word) else (words[-1][1] if words else lang)
                    fix = session.word(keys_of(word, wl), wl)
                    words.append((fix.text if fix else word, wl))
                got = " ".join(w for w, _ in words)
            else:
                got, _ = type_line(session, keys_of(line, lang), lang)
            if got == line:
                right_ok += 1
            elif VERBOSE:
                print(f"TOUCHED  {line!r} -> {got!r}")
            # typed in the wrong layout (one-language lines only: a mixed line has no single "wrong")
            if name == "chat_corpus.txt" and not mixed:
                wrong_total += 1
                other = "en" if lang == "ru" else "ru"
                got, _ = type_line(session, keys_of(line, lang), other)
                if got == line:
                    wrong_ok += 1
                elif VERBOSE:
                    print(f"MISSED   {line!r} -> {got!r}")
    print(f"fixed when typed wrong: {wrong_ok}/{wrong_total}   left alone when typed right: {right_ok}/{right_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
