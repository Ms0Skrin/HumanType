"""Builds the layout detector's data from the raw frequency lists in tools/raw.

Where the raw lists come from ("word count" lines, most frequent first):
  ru_full.txt, en_full.txt — FrequencyWords by Hermit Dave (https://github.com/hermitdave/FrequencyWords,
      content/2018/<lang>/<lang>_full.txt), counted over OpenSubtitles 2018; data under CC BY-SA 4.0.
  uk_full.txt — wordfreq by Robyn Speer (https://github.com/rspeer/wordfreq), written out by
      tools/fetch_uk.py; data under CC BY-SA 4.0 (Wikipedia, OpenSubtitles 2018 and others).

assets/dict/<lang>.words  — "word count" lines, most frequent first, first line "#total N"
assets/dict/<lang>.ngram  — JSON character trigram counts for words, with ^ and $ marking the edges
"""

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "tools" / "raw"
OUT = ROOT / "assets" / "dict"

LANGS = {
    "ru": (re.compile(r"^[а-яё]+(?:-[а-яё]+)?$"), 300_000),
    "en": (re.compile(r"^[a-z]+(?:'[a-z]+)?$"), 200_000),
    # No ы, э, ъ, ё: a word with them is Russian. The apostrophe is a part of the word (п'ять).
    "uk": (re.compile(r"^[а-щьюяєіїґ]+(?:'[а-щьюяєіїґ]+)*(?:-[а-щьюяєіїґ]+(?:'[а-щьюяєіїґ]+)*)?$"), 300_000),
}
MIN_COUNT = 3
# Measured: the full lists (475k / 400k) fix fewer words and add false switches; the tail is mostly noise.


# Ukrainian texts quote Russian ones: a word of the Ukrainian list that Russian has this many times
# more often (per million words) is taken for Russian and left out («что», «только», «привет»).
# Words the two languages share («мама», «так», «на») are about as common in both and stay.
UK_RUSSIAN_RATIO = 8.0
# The list is partly counted over Wikipedia, which is full of abbreviations (дап, мфк, рлс): short
# "words" rarer than this many per million are nearly all of that kind and make false switches.
UK_SHORT_LEN = 3
UK_SHORT_MIN = 5.0
APOSTROPHES = str.maketrans({"ʼ": "'", "’": "'", "`": "'"})


def normalize(word: str, lang: str = "ru") -> str:
    if lang == "uk":
        return word.translate(APOSTROPHES)
    return word.replace("ё", "е")


def per_million(lang: str) -> dict[str, float]:
    """The built list of another language, as occurrences per million words."""
    out: dict[str, float] = {}
    with open(OUT / f"{lang}.words", encoding="utf-8") as f:
        scale = 1e6 / int(f.readline().split()[1])
        for line in f:
            word, count = line.split()
            out[word] = int(count) * scale
    return out


def build(lang: str) -> None:
    pattern, limit = LANGS[lang]
    counts: Counter[str] = Counter()
    with open(RAW / f"{lang}_full.txt", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            word, count = normalize(parts[0].lower(), lang), int(parts[1])
            if count < MIN_COUNT or not pattern.match(word) or len(word) > 30:
                continue
            counts[word] += count
    if lang == "uk":
        russian = per_million("ru")
        scale = 1e6 / sum(counts.values())
        for word in [w for w, n in counts.items() if russian.get(w, 0.0) > UK_RUSSIAN_RATIO * n * scale
                     or (len(w) <= UK_SHORT_LEN and n * scale < UK_SHORT_MIN)]:
            del counts[word]
    top = counts.most_common(limit)
    total = sum(counts.values())

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{lang}.words", "w", encoding="utf-8") as f:
        f.write(f"#total {total}\n")
        for word, count in top:
            f.write(f"{word} {count}\n")

    # Trigrams weighted by log frequency, so the most common words do not drown the rest.
    tri: Counter[str] = Counter()
    for word, count in top:
        weight = math.log1p(count)
        padded = f"^^{word}$"
        for i in range(len(padded) - 2):
            tri[padded[i : i + 3]] += weight
    (OUT / f"{lang}.ngram").write_text(json.dumps({k: round(v, 2) for k, v in tri.items()}, ensure_ascii=False), encoding="utf-8")
    print(f"{lang}: {len(top)} words (of {len(counts)}), {len(tri)} trigrams")


if __name__ == "__main__":
    for lang in sys.argv[1:] or LANGS:
        build(lang)
