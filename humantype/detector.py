"""Deciding whether a typed word belongs to the other keyboard layout.

A word arrives as the keys that were pressed, spelled as the US layout prints them
(`ghbdtn`), together with the layout that was active. Both readings (`ghbdtn` and
`привет`) are scored by three signals:

  * dictionary: is the word a known word form, and how common is it (per million words);
  * character trigrams: how natural the letter sequence is for the language;
  * shape: characters that cannot sit inside, or start, a word of that language (`k.,jdm`).

The word is switched only when the other reading is clearly better; when in doubt it is left
alone. Short words (`z` = «я», `yt` = «не») are decided with the neighbouring words: a
`Session` follows the words as they are typed and may fix a short word together with the next.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .layout import EN_TO_RU, RU_TO_EN

EN_LETTERS = set("abcdefghijklmnopqrstuvwxyz")
RU_LETTERS = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
LETTERS = {"en": EN_LETTERS, "ru": RU_LETTERS}
INNER = {"en": set("'-"), "ru": set("-")}  # allowed inside a word besides letters
EDGE_PUNCT = set(".,!?;:\"'()[]{}<>«»-—/\\|`~@#$%^&*_=+№")
LEADING_OK = set("([{<«\"'-#@*_¿¡")  # what may stand before a word
SINGLE = {"ru": {"в", "и", "с", "к", "у", "о", "я", "а"}, "en": {"a", "i"}}


@dataclass(frozen=True)
class Sensitivity:
    both_known_gap: float  # log10 frequency gap when both readings are dictionary words
    both_known_max_current: float  # ... and the current reading must be at most this common
    known_min_freq: float  # log10 per-million floor for the other reading to count as a real word
    known_ngram_gap: float  # when only the other reading is known: the trigram gap that also suffices
    unknown_ngram_gap: float  # neither reading known: bits per trigram the other must win by
    unknown_min_len: int
    short_min_freq: float  # two-letter words: how common the other reading must be
    short_alone_freq: float  # ... to be switched with no context at all


SENSITIVITY = {
    "careful": Sensitivity(3.0, 0.5, 0.0, 2.5, 3.0, 5, 2.0, 9.0),
    "normal": Sensitivity(2.0, 1.0, -0.7, 1.5, 2.2, 4, 1.5, 3.0),
    "bold": Sensitivity(1.5, 1.5, -1.2, 1.0, 1.6, 3, 1.0, 2.5),
}


@dataclass
class Reading:
    lang: str
    text: str  # the whole token in this layout, punctuation included
    core: str  # the word itself, edge punctuation stripped
    prefix: str
    suffix: str
    valid: bool  # the core is shaped like a word of this language
    known: bool
    freq: float  # log10 per million; -9 when unknown
    ngram: float  # mean log2 probability per trigram


@dataclass
class Decision:
    switch: bool
    target: str  # the language the word should be in
    text: str  # the token as it should read
    reason: str
    current: Reading | None = None
    other: Reading | None = None
    pending: bool = False  # a short word that the next word may still switch
    confident: bool = False  # sure enough of `target` to serve as context for the neighbours


class NgramModel:
    def __init__(self, counts: dict[str, float], alphabet: set[str]) -> None:
        self.counts = counts
        self.context: dict[str, float] = {}
        for tri, n in counts.items():
            self.context[tri[:2]] = self.context.get(tri[:2], 0.0) + n
        self.vocab = len(alphabet) + 1
        self.k = 0.1

    def score(self, word: str) -> float:
        padded = f"^^{word}$"
        total = 0.0
        n = 0
        for i in range(len(padded) - 2):
            tri = padded[i : i + 3]
            num = self.counts.get(tri, 0.0) + self.k
            den = self.context.get(tri[:2], 0.0) + self.k * self.vocab
            total += math.log2(num / den)
            n += 1
        return total / n


def _mixed_case(core: str) -> bool:
    """camelCase and the like; `Нью-Йорк` and `NASA` are fine."""
    for part in core.split("-"):
        if any(c.isupper() for c in part[1:]) and not part.isupper():
            return True
    return False


class Detector:
    def __init__(self, dict_dir: Path) -> None:
        self.freq: dict[str, dict[str, float]] = {}
        self.models: dict[str, NgramModel] = {}
        for lang in ("ru", "en"):
            words: dict[str, float] = {}
            with open(dict_dir / f"{lang}.words", encoding="utf-8") as f:
                total = int(f.readline().split()[1])
                scale = 1e6 / total
                for line in f:
                    word, count = line.split()
                    words[word] = round(math.log10(int(count) * scale), 2)
            self.freq[lang] = words
            counts = json.loads((dict_dir / f"{lang}.ngram").read_text(encoding="utf-8"))
            self.models[lang] = NgramModel(counts, LETTERS[lang])
        self.sensitivity = SENSITIVITY["normal"]
        self.short_words = True
        self.exceptions: set[str] = set()  # key spellings (lowercase, US layout) never to switch

    # --- readings ---

    def reading(self, lang: str, text: str) -> Reading:
        letters, inner = LETTERS[lang], INNER[lang]
        start, end = 0, len(text)
        while start < end and text[start] in EDGE_PUNCT and text[start].lower() not in letters:
            start += 1
        while end > start and text[end - 1] in EDGE_PUNCT and text[end - 1].lower() not in letters:
            end -= 1
        core = text[start:end]
        lower = core.lower()
        valid = (
            bool(core)
            and lower[0] in letters
            and lower[-1] in letters
            and all(c in letters or c in inner for c in lower)
            and all(c in LEADING_OK for c in text[:start])
        )
        key = lower.replace("ё", "е")
        freq = self.freq[lang].get(key) if valid else None
        if valid and len(key) == 1 and key not in SINGLE[lang]:
            freq = None  # word lists are full of stray letters; only real one-letter words count
        return Reading(
            lang=lang,
            text=text,
            core=core,
            prefix=text[:start],
            suffix=text[end:],
            valid=valid,
            known=freq is not None,
            freq=freq if freq is not None else -9.0,
            ngram=self.models[lang].score(key) if valid else -99.0,
        )

    def _confident(self, r: Reading) -> bool:
        letters = sum(c.isalpha() for c in r.core)
        if not r.valid:
            return False
        if r.known:
            # Word lists carry rare scraps (`nj`, `gj`, «ру»): short or rare ones prove nothing.
            if letters == 1:
                return r.core.lower() in SINGLE[r.lang]
            if letters == 2:
                return r.freq >= 2.0
            return r.freq >= -1.5
        return letters >= 5 and r.ngram > self._floor(r.lang) + 1.5

    # --- the decision ---

    def decide(self, keys: str, current: str, context: str | None = None) -> Decision:
        """keys: the token as the US layout spells the pressed keys; current: "en" or "ru";
        context: the language of the neighbouring words, when known."""
        other = "ru" if current == "en" else "en"
        cur = self.reading(current, keys if current == "en" else keys.translate(EN_TO_RU))
        alt = self.reading(other, keys if other == "en" else keys.translate(EN_TO_RU))

        def no(reason: str, pending: bool = False) -> Decision:
            return Decision(False, current, cur.text, reason, cur, alt, pending, self._confident(cur))

        def yes(reason: str) -> Decision:
            return Decision(True, other, alt.text, reason, cur, alt, False, True)

        if keys.lower() in self.exceptions:
            return no("exception")
        if any(c.isdigit() for c in keys):
            return no("digits")
        if "://" in keys or keys.lower().startswith("www"):
            return no("address")
        if not alt.valid:
            return no("other reading is not a word")
        if _mixed_case(alt.core):
            return no("mixed case")
        if alt.core.isupper() and sum(c.isalpha() for c in alt.core) >= 2 and not alt.known:
            return no("abbreviation")  # РСФСР, NASA: only a dictionary word in capitals is a typo

        # Context moves the bar: the neighbours speaking the other language make a switch easier.
        s = self.sensitivity
        if context == other:
            s = SENSITIVITY["bold"]
        elif context == current:
            s = SENSITIVITY["careful"]
        letters = sum(1 for c in alt.core if c.isalpha())

        if letters <= 2:
            if not self.short_words:
                return no("short words off")
            if letters == 1:
                fits = alt.core.lower() in SINGLE[other] and (not cur.valid or cur.core.lower() not in SINGLE[current])
            else:
                fits = alt.known and alt.freq >= s.short_min_freq and (
                    not cur.known or alt.freq - cur.freq >= s.both_known_gap
                )
            if not fits:
                return no("short word, unsure")
            if context == other:
                return yes("short word, neighbours agree")
            if not cur.valid and alt.known:
                return yes("short word, current reading is not a word")
            if letters == 2 and context is None and not cur.known and alt.freq >= s.short_alone_freq:
                return yes("very common short word")
            return no("short word, waiting for context", pending=context != current)

        if not cur.valid:
            # The current reading has signs inside that no word of its language has (`k.,jdm`).
            if alt.known:
                return yes("current reading is not a word")
            # A dot inside is more often a file, a site or an abbreviation (`file.txt`, `i.e.`).
            if "." not in cur.core and alt.ngram > self._floor(other) + 2.0:
                return yes("current reading is not a word, letters fit")
            return no("neither reading is a word")

        if alt.known and cur.known:
            if alt.freq - cur.freq >= s.both_known_gap and cur.freq <= s.both_known_max_current:
                return yes("other word is far more common")
            return no("both are words")
        if alt.known:
            if alt.freq >= s.known_min_freq or alt.ngram - cur.ngram >= s.known_ngram_gap:
                return yes("other reading is a known word")
            return no("other reading is a rare word")
        if cur.known:
            return no("current reading is a known word")
        if letters >= s.unknown_min_len and alt.ngram - cur.ngram >= s.unknown_ngram_gap and alt.ngram > self._floor(other):
            return yes("letters fit the other language")
        return no("unsure")

    def _floor(self, lang: str) -> float:
        """A trigram score below which a sequence does not look like the language at all."""
        return -7.0 if lang == "ru" else -6.5


# --- following a run of words ---


@dataclass
class Fix:
    erase: int  # characters to delete before the cursor (the cursor sits right after the word)
    text: str  # what to type in their place
    layout: str  # the layout to switch the window to
    original: str = ""  # the text being replaced, as it was on screen (for undo)
    keys: list[str] = field(default_factory=list)  # key spellings of the fixed words


@dataclass
class _Undecided:
    decision: Decision
    delimiter: str  # what was typed after it
    keys: str


MAX_LOOKBACK = 3


@dataclass
class Session:
    """Follows the words of one stretch of typing (until a click, Enter or a focus change).

    Words the detector could not settle (short ones, rare ones) wait here; when a following
    word switches with confidence, the waiting words that read well in that language are
    fixed together with it."""

    detector: Detector
    context: str | None = None
    undecided: list[_Undecided] = field(default_factory=list)

    def reset(self) -> None:
        self.context = None
        self.undecided.clear()

    def _can_follow(self, d: Decision, target: str) -> bool:
        alt = d.other
        if d.switch or d.confident or alt is None or alt.lang != target or not alt.valid:
            return False
        if _mixed_case(alt.core) or any(c.isdigit() for c in alt.text):
            return False
        return d.pending or alt.known or alt.ngram > self.detector._floor(target) + 1.0

    def word(self, keys: str, layout: str, delimiter: str = " ") -> Fix | None:
        """Called when a word is finished, before its delimiter reaches the window."""
        d = self.detector.decide(keys, layout, self.context)
        if d.switch:
            original, text, fixed_keys = d.current.text, d.text, [keys]
            # Walk back over the words still waiting, nearest first, while they agree.
            for waiting in reversed(self.undecided):
                if waiting.keys.lower() in self.detector.exceptions or not self._can_follow(waiting.decision, d.target):
                    break
                original = waiting.decision.current.text + waiting.delimiter + original
                text = waiting.decision.other.text + waiting.delimiter + text
                fixed_keys.insert(0, waiting.keys)
            self.undecided.clear()
            self.context = d.target
            return Fix(len(original), text, d.target, original, fixed_keys)
        if d.confident:
            self.undecided.clear()
            self.context = d.target
        else:
            self.undecided = (self.undecided + [_Undecided(d, delimiter, keys)])[-MAX_LOOKBACK:]
            if not d.pending:
                self.context = None
        return None


def keys_of(text: str, lang: str) -> str:
    """How the US layout spells the keys that type `text` in `lang` (for tests and the fixer)."""
    return text if lang == "en" else text.translate(RU_TO_EN)
