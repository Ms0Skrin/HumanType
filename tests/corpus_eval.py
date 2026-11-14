"""Runs anywhere: how often the detector fixes a line typed in the wrong layout, and how often it
touches a line typed right. Each line is typed as a person would: the same keys throughout, the
layout flipping only when a fix switches it.
    python tests/corpus_eval.py [-v]

The first line of the output is the Russian and English corpus with two languages in use, as a
computer with no Ukrainian layout has it. The lines after it are with Ukrainian in use too: the same
corpus again (the numbers must not get worse), the same corpus typed in the Ukrainian layout, and
the Ukrainian corpus typed in each of the three layouts.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from humantype.detector import Detector, Session, keys_of  # noqa: E402
from humantype.layout import text_of  # noqa: E402

VERBOSE = "-v" in sys.argv


def type_line(session: Session, keys: str, layout: str) -> tuple[str, str]:
    """Types the key spellings word by word; returns the screen text and the final layout."""
    screen = ""
    session.reset()
    session.prefer = None  # every line starts afresh: no Cyrillic layout typed in before it
    for word in keys.split(" "):
        if not word:
            screen += " "
            continue
        shown = text_of(word, layout)
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


def ru_en(det: Detector) -> str:
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
    return f"fixed when typed wrong: {wrong_ok}/{wrong_total}   left alone when typed right: {right_ok}/{right_total}"


def lines_of(name: str, only: str | None = None) -> list[tuple[str, str]]:
    """(line, its language) for the lines of a corpus written in one language."""
    out = []
    for line in (ROOT / "tests" / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        lang = only or lang_of(line)
        mixed = any("а" <= c.lower() <= "я" for c in line) and any("a" <= c.lower() <= "z" for c in line)
        if only or not mixed:
            out.append((line, lang))
    return out


def typed_in(det: Detector, lines: list[tuple[str, str]], layout: str, label: str) -> str:
    """Types every line with the keys of its own language while `layout` is on: the text must come
    out as written. Words that the two Cyrillic layouts type alike need no fix and get none."""
    session = Session(det)
    ok = 0
    for line, lang in lines:
        got, _ = type_line(session, keys_of(line, lang), layout)
        if got == line:
            ok += 1
        elif VERBOSE:
            print(f"{label:8} {line!r} -> {got!r}")
    return f"{ok}/{len(lines)}"


def main() -> int:
    det = Detector(ROOT / "assets" / "dict")
    print(ru_en(det))
    if not det.available("uk"):
        return 0
    det.set_langs(("ru", "en", "uk"))
    print("with Ukrainian in use:")
    print("  ru/en corpus:", ru_en(det))
    chat = lines_of("chat_corpus.txt")
    ru = [(line, lang) for line, lang in chat if lang == "ru"]
    en = [(line, lang) for line, lang in chat if lang == "en"]
    uk = lines_of("uk_corpus.txt", "uk")
    print(f"  Russian typed in the Ukrainian layout, fixed: {typed_in(det, ru, 'uk', 'RU-IN-UK')}"
          f"   English typed in the Ukrainian layout, fixed: {typed_in(det, en, 'uk', 'EN-IN-UK')}")
    print(f"  Ukrainian corpus: left alone when typed right: {typed_in(det, uk, 'uk', 'TOUCHED')}"
          f"   fixed when typed in English: {typed_in(det, uk, 'en', 'UK-IN-EN')}"
          f"   fixed when typed in Russian: {typed_in(det, uk, 'ru', 'UK-IN-RU')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
