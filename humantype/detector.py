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

There may be more than two languages (English, Russian, Ukrainian): the word is then read in every
language in use but the current one, the best of those readings is picked (`Detector._best`) and
the decision is made between it and the current reading, as with two. A reading that gives the
very text already on screen («мама» in the Russian and the Ukrainian layout) is never a reason to
switch, so words the two languages share do not throw the layout back and forth.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .layout import CYRILLIC, keys_of, text_of  # noqa: F401  (keys_of: for tests and the fixer)

try:  # Russian morphology: every form of every dictionary word, not only the frequent ones
    import pymorphy3
    import pymorphy3_dicts_ru  # by path: entry points do not survive PyInstaller
except ImportError:
    pymorphy3 = None
try:  # the same for Ukrainian, when its dictionaries are installed
    import pymorphy3_dicts_uk
except ImportError:
    pymorphy3_dicts_uk = None

MID_LEN = 5  # letters after which a word still being typed may be switched
MID_MIN_FREQ = 0.0  # ... if a word at least this common begins so in the other layout
MID_NGRAM_GAP = 1.5  # ... and its letters fit that language this much better
MORPH_FREQ = -1.0  # log10 per million given to a form only the morphology knows

EN_LETTERS = set("abcdefghijklmnopqrstuvwxyz")
RU_LETTERS = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
UK_LETTERS = set("абвгґдеєжзиіїйклмнопрстуфхцчшщьюя")
LETTERS = {"en": EN_LETTERS, "ru": RU_LETTERS, "uk": UK_LETTERS}
INNER = {"en": set("'-"), "ru": set("-"), "uk": set("-'ʼ")}  # allowed inside a word besides letters
BEST_FREQ_TIE = 1.5  # readings in two languages this close (log10 frequency) are told apart by the neighbours
BEST_NGRAM_TIE = 1.0
SAME_WORD_GAP = 3.0  # log10: a word two languages spell alike, this much rarer in one, is not that one's word
SAME_TEXT_FREQ = 0.0  # log10 per million: a word only the other Cyrillic language has moves the layout to it
SAME_TEXT_FREQ_CAREFUL = 1.0  # ... when the neighbours speak the current one  # ... or, when neither is a known word, this close in bits per trigram
EDGE_PUNCT = set(".,!?;:\"'()[]{}<>«»-—/\\|`~@#$%^&*_=+№")
LEADING_OK = set("([{<«\"'-#@*_¿¡")  # what may stand before a word
# «ж» and «б» are words too, but their keys are signs in the other layouts (; ,): left out. «є» is
# too common to leave out (its key is the apostrophe): it is fixed only together with a neighbour.
SINGLE = {"ru": {"в", "и", "с", "к", "у", "о", "я", "а"}, "en": {"a", "i"},
          "uk": {"в", "і", "й", "з", "у", "о", "я", "а", "є"}}


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
    alts: dict[str, Reading] = field(default_factory=dict)  # the word in every other language in use


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


def _key(lower: str, lang: str) -> str:
    """The word as the word list spells it."""
    if lang == "uk":
        return lower.replace("ʼ", "'")
    return lower.replace("ё", "е")


def _mixed_case(core: str) -> bool:
    """camelCase and the like; `Нью-Йорк` and `NASA` are fine."""
    for part in core.split("-"):
        if any(c.isupper() for c in part[1:]) and not part.isupper():
            return True
    return False


class Detector:
    def __init__(self, dict_dir: Path, morphology: bool = True, langs: tuple[str, ...] = ("ru", "en")) -> None:
        self.dict_dir = dict_dir
        self.morphology = morphology
        self.freq: dict[str, dict[str, float]] = {}
        # How common the commonest word with this beginning is: for judging a word still being typed.
        self.starts: dict[str, dict[str, float]] = {}
        self.models: dict[str, NgramModel] = {}
        self.morphs: dict[str, object] = {}
        # The languages in use, the first the one to take when nothing tells two readings apart.
        # Russian and English always; Ukrainian when its layout is there (see `set_langs`).
        self.langs: tuple[str, ...] = ()
        self.set_langs(langs)
        self.morph = self.morphs.get("ru")
        self.sensitivity = SENSITIVITY["normal"]
        self.short_words = True
        self.exceptions: set[str] = set()  # key spellings (lowercase, US layout) never to switch
        self.soft_exceptions: set[str] = set()  # undone once: left alone until the app restarts
        self.learned: dict[str, str] = {}  # key spellings the person flipped by hand -> the language they want

    def available(self, lang: str) -> bool:
        """The word list of the language is there to load."""
        return lang in self.freq or (lang in LETTERS and (self.dict_dir / f"{lang}.words").exists()
                                     and (self.dict_dir / f"{lang}.ngram").exists())

    def set_langs(self, langs: tuple[str, ...] | list[str]) -> None:
        """The languages to tell apart from now on. Their word lists are loaded when first asked
        for and kept; a language with no word list is left out."""
        wanted = tuple(dict.fromkeys(lang for lang in langs if self.available(lang)))
        for lang in wanted:
            if lang not in self.freq:
                self._load(lang)
        self.langs = wanted

    def _load(self, lang: str) -> None:
        words: dict[str, float] = {}
        with open(self.dict_dir / f"{lang}.words", encoding="utf-8") as f:
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
        counts = json.loads((self.dict_dir / f"{lang}.ngram").read_text(encoding="utf-8"))
        self.models[lang] = NgramModel(counts, LETTERS[lang])
        if pymorphy3 and self.morphology:
            try:
                if lang == "ru":
                    self.morphs[lang] = pymorphy3.MorphAnalyzer(pymorphy3_dicts_ru.get_path())
                elif lang == "uk" and pymorphy3_dicts_uk:
                    self.morphs[lang] = pymorphy3.MorphAnalyzer(pymorphy3_dicts_uk.get_path(), lang="uk")
            except Exception:  # a broken dictionary must not stop the detector
                if lang == "ru":
                    raise

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
        key = _key(lower, lang)
        freq = self.freq[lang].get(key) if valid else None
        if valid and len(key) == 1 and key not in SINGLE[lang]:
            freq = None  # word lists are full of stray letters; only real one-letter words count
        morph = self.morph if lang == "ru" else self.morphs.get(lang)
        if freq is None and valid and len(key) >= 3 and morph and morph.word_is_known(key):
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

    def others(self, current: str) -> list[str]:
        """The languages a word typed in `current` may belong to instead."""
        found = [lang for lang in self.langs if lang != current]
        return found or [lang for lang in ("en", "ru") if lang != current][:1]

    def _best(self, cur: Reading, alts: list[Reading], context: str | None, prefer: str | None) -> Reading:
        """The reading to weigh against the current one, when there are several languages to choose
        from: a known word before letters that only look right, the more common word before the
        rarer. Readings that are about as good (the same word in Russian and Ukrainian, as a rule)
        go by the neighbours' language, then by the Cyrillic layout last typed in. A reading that
        takes in more of the token comes first (the keys of «ещё» are `to` and a sign: it is «ещё», not `to`)."""
        if len(alts) == 1:
            return alts[0]

        def tier(r: Reading) -> int:
            return 3 if r.valid and r.known else 2 if r.valid else 1

        top = max(tier(r) for r in alts)
        best = [r for r in alts if tier(r) == top]
        if top > 1:
            longest = max(len(r.core) for r in best)
            best = [r for r in best if len(r.core) == longest]
        # The same word in two languages («село»): how common it is in each says little, the lists
        # being counted over different texts, unless one has it a thousand times more often: then
        # the other list has merely picked it up from quotes («зараз» in the Russian one).
        # Otherwise only readings about as good are a choice.
        if top == 3:
            high = max(r.freq for r in best)
            gap = BEST_FREQ_TIE if len({r.text for r in best}) > 1 else SAME_WORD_GAP
            best = [r for r in best if high - r.freq <= gap]
        elif len({r.text for r in best}) > 1:
            if top == 2:
                high = max(r.ngram for r in best)
                best = [r for r in best if high - r.ngram <= BEST_NGRAM_TIE]
        for lang in (context, prefer):
            for r in best:
                if r.lang == lang:
                    return r
        if len({r.text for r in best}) == 1 or top == 2:
            return best[0]  # nothing tells them apart: the order of `langs`
        if top == 3:
            return max(best, key=lambda r: r.freq)
        return best[0]

    def decide(self, keys: str, current: str, context: str | None = None, prefer: str | None = None) -> Decision:
        """keys: the token as the US layout spells the pressed keys; current: the language of the
        layout it was typed in; context: the language of the neighbouring words, when known;
        prefer: the Cyrillic layout last typed in, to take when Russian and Ukrainian read alike."""
        cur = self.reading(current, text_of(keys, current))
        alts = [self.reading(lang, text_of(keys, lang)) for lang in self.others(current)]
        taught = self.learned.get(keys.lower())  # a word taught by hand goes where it was sent
        alt = next((r for r in alts if r.lang == taught), None) or self._best(cur, alts, context, prefer)
        other = alt.lang

        by_lang = {r.lang: r for r in alts}

        def no(reason: str, pending: bool = False) -> Decision:
            return Decision(False, current, cur.text, reason, cur, alt, pending, self._confident(cur), by_lang)

        def yes(reason: str) -> Decision:
            return Decision(True, other, alt.text, reason, cur, alt, False, True, by_lang)

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

        if alt.text == cur.text:
            # The Russian and the Ukrainian layout type this word alike: nothing to fix on screen.
            # A word both languages have («мама», «так») says nothing. A word only the other one
            # has («привет» in the Ukrainian layout, «дякую» in the Russian) says which language is
            # being typed: the layout follows it, so the next «ы» or «і» comes out right.
            mine = cur.known and (context == current or alt.freq - cur.freq < SAME_WORD_GAP)
            if mine or not alt.known or sum(c.isalpha() for c in alt.core) < 3:
                return no("same text in both layouts")
            if alt.freq >= (SAME_TEXT_FREQ_CAREFUL if context == current else SAME_TEXT_FREQ):
                return yes("a word of the other Cyrillic layout")
            return no("same text in both layouts")

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
            if not cur.valid and alt.known and letters == 2:  # one letter on a sign's key («є»): with a neighbour only
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
        found = self.start_target(keys, current)
        return found[0] if found else None

    def start_target(self, keys: str, current: str, context: str | None = None,
                     prefer: str | None = None) -> tuple[str, str] | None:
        """`start_switch` with the language to switch to: (text, language). With several languages
        to choose from, the one whose words begin so more often; the neighbours' language and the
        Cyrillic layout last typed in when the beginnings read alike."""
        if len(keys) != MID_LEN or any(c.isdigit() for c in keys):
            return None
        cur = text_of(keys, current)
        found: list[tuple[float, str, str]] = []
        for other in self.others(current):
            alt = self._start_switch(keys, current, other)
            if alt is not None and alt != cur:
                found.append((self.starts[other].get(_key(alt.lower(), other), -9.0), alt, other))
        if not found:
            return None
        high = max(freq for freq, _, _ in found)
        close = [(freq, alt, other) for freq, alt, other in found if high - freq <= BEST_FREQ_TIE]
        for lang in (context, prefer):
            for _, alt, other in close:
                if other == lang:
                    return alt, other
        _, alt, other = max(close, key=lambda item: item[0])
        return alt, other

    def _start_switch(self, keys: str, current: str, other: str) -> str | None:
        cur = text_of(keys, current)
        alt = text_of(keys, other)
        low = alt.lower()
        if alt[1:] != low[1:] or not all(c in LETTERS[other] for c in low):
            return None  # capitals inside (an abbreviation, camelCase) or signs: wait for the whole word
        lowered = keys.lower()
        if any(word.startswith(lowered) for word in self.exceptions | self.soft_exceptions):
            return None
        if any(word.startswith(lowered) and lang == current for word, lang in self.learned.items()):
            return None
        if self.starts[other].get(_key(low, other), -9.0) < MID_MIN_FREQ:
            return None
        cur_low = cur.lower()
        if all(c in LETTERS[current] for c in cur_low):
            if _key(cur_low, current) in self.starts[current] or cur_low in self.freq[current]:
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
        return -7.0 if lang in CYRILLIC else -6.5

    def flip_target(self, keys: str, source: str, prefer: str | None = None) -> str:
        """The language a word typed in `source` goes to when it is flipped by hand. With two
        languages, the other one; with more, the one the word reads best in."""
        others = self.others(source)
        if len(others) == 1:
            return others[0]
        cur = self.reading(source, text_of(keys, source))
        alts = [self.reading(lang, text_of(keys, lang)) for lang in others]
        best = self._best(cur, alts, None, prefer)
        if best.text != cur.text and best.valid and best.known:
            return best.lang
        # Nothing reads as a word: Latin goes to the Cyrillic layout last typed in, Cyrillic to Latin.
        if source != "en" and "en" in others:
            return "en"
        return prefer if prefer in others else others[0]


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
    prefer: str | None = None  # the Cyrillic layout last typed in: outlives `reset`

    def _saw(self, layout: str) -> None:
        if layout in CYRILLIC:
            self.prefer = layout

    def reset(self) -> None:
        self.context = None
        self.undecided.clear()

    def _can_follow(self, d: Decision, target: str) -> bool:
        alt = d.alts.get(target) or (d.other if d.other is not None and d.other.lang == target else None)
        if d.switch or d.confident or alt is None or not alt.valid:
            return False
        if _mixed_case(alt.core) or any(c.isdigit() for c in alt.text):
            return False
        if alt is d.other or alt.text == d.other.text:
            return d.pending or alt.known or alt.ngram > self.detector._floor(target) + 1.0
        # Weighed against another language's reading («ті» when the neighbour proved Russian): by its own word.
        letters = sum(c.isalpha() for c in alt.core)
        if letters == 1:
            return alt.core.lower() in SINGLE[target]
        return alt.known and (letters > 2 or alt.freq >= SENSITIVITY["bold"].short_min_freq)

    def _fix(self, original: str, text: str, target: str, keys: str) -> Fix:
        """The fix for a word that switches to `target`, together with the waiting words that follow it."""
        fixed_keys = [keys]
        # Walk back over the words still waiting, nearest first, while they agree.
        for waiting in reversed(self.undecided):
            if self.detector.is_exception(waiting.keys) or not self._can_follow(waiting.decision, target):
                break
            original = waiting.decision.current.text + waiting.delimiter + original
            text = (waiting.decision.alts.get(target) or waiting.decision.other).text + waiting.delimiter + text
            fixed_keys.insert(0, waiting.keys)
        self.undecided.clear()
        self.context = target
        self._saw(target)
        return Fix(len(original), text, target, original, fixed_keys)

    def start(self, keys: str, layout: str) -> Fix | None:
        """Called when a word still being typed reaches MID_LEN letters: the fix for what is typed
        so far, if it clearly belongs to the other layout."""
        self._saw(layout)
        found = self.detector.start_target(keys, layout, self.context, self.prefer)
        if found is None:
            return None
        text, target = found
        return self._fix(text_of(keys, layout), text, target, keys)

    def word(self, keys: str, layout: str, delimiter: str = " ") -> Fix | None:
        """Called when a word is finished, before its delimiter reaches the window."""
        self._saw(layout)
        d = self.detector.decide(keys, layout, self.context, self.prefer)
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
