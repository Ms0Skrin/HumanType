"""Writes the raw Ukrainian frequency list (tools/raw/uk_full.txt) for tools/build_dict.py.

The Russian and English lists come from FrequencyWords (OpenSubtitles 2018). Its Ukrainian list is
of little use: about four words in ten there are Russian («что» is nearly as common as «що»). So
Ukrainian comes from wordfreq (https://github.com/rspeer/wordfreq, data under CC BY-SA 4.0), which
blends Wikipedia, OpenSubtitles 2018 and other sources and drops what only one of them has.

    pip install wordfreq && python tools/fetch_uk.py

wordfreq gives a share of all words; it is written as occurrences per 1e9 words, to have the
"word count" lines the other lists have."""

from pathlib import Path

import wordfreq

RAW = Path(__file__).resolve().parent / "raw"
SCALE = 1e9


def main() -> None:
    RAW.mkdir(exist_ok=True)
    words = wordfreq.get_frequency_dict("uk", "large")
    lines = [f"{word} {round(freq * SCALE)}" for word, freq in sorted(words.items(), key=lambda item: -item[1])
             if len(word.split()) == 1 and round(freq * SCALE) > 0]
    (RAW / "uk_full.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"uk: {len(lines)} words, wordfreq {getattr(wordfreq, '__version__', '')}".strip())


if __name__ == "__main__":
    main()
