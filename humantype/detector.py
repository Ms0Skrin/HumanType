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

try:  # Russian morphology: every form of every dictionary word, not only the frequent ones
    import pymorphy3
    import pymorphy3_dicts_ru  # by path: entry points do not survive PyInstaller
except ImportError:
    pymorphy3 = None

MID_LEN = 5  # letters after which a word still being typed may be switched
MID_MIN_FREQ = 0.0  # ... if a word at least this common begins so in the other layout
MID_NGRAM_GAP = 1.5  # ... and its letters fit that language this much better
MORPH_FREQ = -1.0  # log10 per million given to a form only the morphology knows

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

    def prefix_score(self, start: str) -> float:
        """The same for the beginning of a word still being typed: no end is counted."""
        padded = f"^^{start}"
        total = 0.0
        for i in range(len(padded) - 2):
            tri = padded[i : i + 3]
            total += math.log2((self.counts.get(tri, 0.0) + self.k) / (self.context.get(tri[:2], 0.0) + self.k * self.vocab))
        return total / (len(padded) - 2)


def _mixed_case(core: str) -> bool:
    """camelCase and the like; `Нью-Йорк` and `NASA` are fine."""
    for part in core.split("-"):
        if any(c.isupper() for c in part[1:]) and not part.isupper():
            return True
    return False


class Detector:
    def __init__(self, dict_dir: Path, morphology: bool = True) -> None:
        self.freq: dict[str, dict[str, float]] = {}
        # How common the commonest word with this beginning is: for judging a word still being typed.
        self.starts: dict[str, dict[str, float]] = {}
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
            starts: dict[str, float] = {}
            for word, freq in words.items():
                if len(word) >= MID_LEN and freq > starts.get(word[:MID_LEN], -9.0):
                    starts[word[:MID_LEN]] = freq
            self.starts[lang] = starts
            counts = json.loads((dict_dir / f"{lang}.ngram").read_text(encoding="utf-8"))
            self.models[lang] = NgramModel(counts, LETTERS[lang])
        self.morph = pymorphy3.MorphAnalyzer(pymorphy3_dicts_ru.get_path()) if pymorphy3 and morphology else None
        self.sensitivity = SENSITIVITY["normal"]
        self.short_words = True
        self.exceptions: set[str] = set()  # key spellings (lowercase, US layout) never to switch
        self.soft_exceptions: set[str] = set()  # undone once: left alone until the app restarts
        self.learned: dict[str, str] = {}  # key spellings the person flipped by hand -> the language they want

    def is_exception(self, keys: str) -> bool:
        keys = keys.lower()
        return keys in self.exceptions or keys in self.soft_exceptions

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
        if freq is None and valid and lang == "ru" and len(key) >= 3 and self.morph and self.morph.word_is_known(key):
            freq = MORPH_FREQ
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

        if self.is_exception(keys):
            return no("exception")
        if self.learned.get(keys.lower()) == other and alt.valid:
            return yes("learned")
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
        if alt.core.isupper() and 2 <= sum(c.isalpha() for c in alt.core) <= 4 and context != other:
            return no("abbreviation")  # МСК is not a typo of VCR, nor РГПУ of HUGE

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
            # A dot inside is more often a file, a site or an abbreviation (`file.txt`, `i.e.`),
            # but `ю` sits on that key too (`dntrf.oe.` = «втекающую»).
            if not self._dotted(cur) and alt.ngram > self._floor(other) + 2.0:
                return yes("current reading is not a word, letters fit")
            return no("neither reading is a word")

        if alt.known and cur.known:
            if alt.freq - cur.freq >= s.both_known_gap and cur.freq <= s.both_known_max_current:
                return yes("other word is far more common")
            # The current word is only a piece of what was typed: its edge signs are letters of the
            # other word (`'ne` = «эту», `to\`` = «ещё», `{el` = «худ»).
            # A dot or a comma at the edge is punctuation far more often than «ю» or «б» (`it.`).
            edges = cur.prefix + cur.suffix
            if letters > sum(c.isalpha() for c in cur.core) and alt.freq >= s.short_min_freq and not (
                cur.prefix and cur.suffix
            ) and not set(edges) & set(".,"):
                return yes("current word is cut from the other")
            return no("both are words")
        if alt.known:
            # A rare word still wins over letters that do not look like the current language at all.
            if alt.freq >= s.known_min_freq or alt.ngram - cur.ngram >= s.known_ngram_gap or (
                letters >= 4 and cur.ngram < self._floor(current)
            ):
                return yes("other reading is a known word")
            return no("other reading is a rare word")
        if cur.known:
            return no("current reading is a known word")
        if letters >= s.unknown_min_len and alt.ngram - cur.ngram >= s.unknown_ngram_gap and alt.ngram > self._floor(other):
            return yes("letters fit the other language")
        return no("unsure")

    def start_switch(self, keys: str, current: str) -> str | None:
        """The first MID_LEN keys of a word still being typed: the text they should read in the other
        layout, or None. Far stricter than `decide`: there is no whole word to look up yet, so the
        other reading must begin a common word and the current one must begin none."""
        if len(keys) != MID_LEN or any(c.isdigit() for c in keys):
            return None
        other = "ru" if current == "en" else "en"
        cur = keys if current == "en" else keys.translate(EN_TO_RU)
        alt = keys if other == "en" else keys.translate(EN_TO_RU)
        low = alt.lower()
        if alt[1:] != low[1:] or not all(c in LETTERS[other] for c in low):
            return None  # capitals inside (an abbreviation, camelCase) or signs: wait for the whole word
        lowered = keys.lower()
        if any(word.startswith(lowered) for word in self.exceptions | self.soft_exceptions):
            return None
        if any(word.startswith(lowered) and lang == current for word, lang in self.learned.items()):
            return None
        if self.starts[other].get(low.replace("ё", "е"), -9.0) < MID_MIN_FREQ:
            return None
        cur_low = cur.lower()
        if all(c in LETTERS[current] for c in cur_low):
            if cur_low.replace("ё", "е") in self.starts[current] or cur_low in self.freq[current]:
                return None
            gap = self.models[other].prefix_score(low) - self.models[current].prefix_score(cur_low)
            if gap < MID_NGRAM_GAP:
                return None
        elif cur_low.strip("".join(EDGE_PUNCT)) in self.freq[current]:
            return None  # a short word with a comma or a dot after it (`Duke,`)
        return alt

    def _dotted(self, r: Reading) -> bool:
        """Reads as dotted English (`file.txt`, `github.io`, `i.e`): every part is a word or short."""
        if r.lang != "en" or "." not in r.core:
            return False
        parts = r.core.lower().split(".")
        return all(p and (len(p) <= 4 or p in self.freq["en"]) for p in parts)

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
    last: Decision | None = None  # the decision on the word just finished, for the journal

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

    def _fix(self, original: str, text: str, target: str, keys: str) -> Fix:
        """The fix for a word that switches to `target`, together with the waiting words that follow it."""
        fixed_keys = [keys]
        # Walk back over the words still waiting, nearest first, while they agree.
        for waiting in reversed(self.undecided):
            if self.detector.is_exception(waiting.keys) or not self._can_follow(waiting.decision, target):
                break
            original = waiting.decision.current.text + waiting.delimiter + original
            text = waiting.decision.other.text + waiting.delimiter + text
            fixed_keys.insert(0, waiting.keys)
        self.undecided.clear()
        self.context = target
        return Fix(len(original), text, target, original, fixed_keys)

    def start(self, keys: str, layout: str) -> Fix | None:
        """Called when a word still being typed reaches MID_LEN letters: the fix for what is typed
        so far, if it clearly belongs to the other layout."""
        text = self.detector.start_switch(keys, layout)
        if text is None:
            return None
        original = keys if layout == "en" else keys.translate(EN_TO_RU)
        return self._fix(original, text, "ru" if layout == "en" else "en", keys)

    def word(self, keys: str, layout: str, delimiter: str = " ") -> Fix | None:
        """Called when a word is finished, before its delimiter reaches the window."""
        d = self.detector.decide(keys, layout, self.context)
        self.last = d
        if d.switch:
            return self._fix(d.current.text, d.text, d.target, keys)
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
