"""Builds the layout detector's data from the raw frequency lists in tools/raw.

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
}
MIN_COUNT = 3
# Measured: the full lists (475k / 400k) fix fewer words and add false switches; the tail is mostly noise.


def normalize(word: str) -> str:
    return word.replace("ё", "е")


def build(lang: str) -> None:
    pattern, limit = LANGS[lang]
    counts: Counter[str] = Counter()
    with open(RAW / f"{lang}_full.txt", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            word, count = parts[0].lower(), int(parts[1])
            if count < MIN_COUNT or not pattern.match(word) or len(word) > 30:
                continue
            counts[normalize(word)] += count
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
