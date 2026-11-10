"""What the other fix hotkeys do to a text: transliteration (privet ↔ привет) and letter case.
No Windows here: the functions take a text and give the new one."""

from .layout import LANG_EN, LANG_RU

# Latin → Cyrillic: the longest spelling first.
_TO_RU = [
    ("shch", "щ"), ("sch", "щ"), ("yo", "ё"), ("jo", "ё"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"),
    ("sh", "ш"), ("yu", "ю"), ("ju", "ю"), ("ya", "я"), ("ja", "я"), ("eh", "э"), ("iy", "ий"), ("yy", "ый"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("w", "в"), ("g", "г"), ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"),
    ("j", "й"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"),
    ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "ц"), ("q", "к"), ("x", "кс"), ("y", "ы"), ("'", "ь"),
]
_TO_EN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}
_RU_VOWELS = set("аеёиоуыэюя")


def _is_cyrillic(c: str) -> bool:
    return "а" <= c.lower() <= "я" or c in "ёЁ"


def _cased(new: str, source: str, shout: bool) -> str:
    """`new` in the case of `source`, the letters it stands for: Zh → Ж, Ж → Zh, and ZH inside a word in capitals."""
    if not source[:1].isupper():
        return new
    return new.upper() if shout or (len(source) > 1 and source.isupper()) else new[:1].upper() + new[1:]


def _to_ru(text: str) -> str:
    out: list[str] = []
    low = text.lower()
    i = 0
    while i < len(text):
        for latin, cyr in _TO_RU:
            if low.startswith(latin, i):
                # A "y" after a vowel is «й» (moy → мой, kray → край), before one it is caught above (ya, yu, yo).
                if latin == "y" and out and out[-1].lower() in _RU_VOWELS:
                    cyr = "й"
                source = text[i : i + len(latin)]
                shout = source.isupper() and (text[i + len(latin) : i + len(latin) + 1].isupper() or (i > 0 and text[i - 1].isupper()))
                out.append(_cased(cyr, source, shout))
                i += len(latin)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _to_en(text: str) -> str:
    out: list[str] = []
    for i, c in enumerate(text):
        latin = _TO_EN.get(c.lower())
        if latin is None:
            out.append(c)
            continue
        shout = (text[i + 1 : i + 2].isupper() and _is_cyrillic(text[i + 1 : i + 2])) or (i > 0 and text[i - 1].isupper())
        out.append(_cased(latin, c, shout))
    return "".join(out)


def translit(text: str) -> tuple[str, int | None]:
    """privet → привет, привет → privet: by whichever letters the text has more of."""
    latin = sum(1 for c in text if "a" <= c.lower() <= "z")
    cyr = sum(1 for c in text if _is_cyrillic(c))
    if not latin and not cyr:
        return text, None
    if latin >= cyr:
        return _to_ru(text), LANG_RU
    return _to_en(text), LANG_EN


def recase(text: str) -> tuple[str, int | None]:
    """Caps Lock left on (пРИВЕТ → Привет) is put right; otherwise the case goes round:
    привет → Привет → ПРИВЕТ → привет."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text, None
    words = [[c for c in word if c.isalpha()] for word in text.split()]
    words = [word for word in words if len(word) > 1]
    if len(letters) > 1 and letters[0].islower() and (
        all(c.isupper() for c in letters[1:]) or words and all(w[0].islower() and all(c.isupper() for c in w[1:]) for w in words)
    ):
        return text.swapcase(), None  # typed with Caps Lock on: a whole sentence, or word by word
    if all(c.islower() for c in letters):
        first = next(i for i, c in enumerate(text) if c.isalpha())
        return text[:first] + text[first].upper() + text[first + 1 :], None
    if all(c.isupper() for c in letters):
        return text.lower(), None
    if letters[0].isupper() and all(c.islower() for c in letters[1:]):
        return text.upper(), None
    return text.lower(), None
