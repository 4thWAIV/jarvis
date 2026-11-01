# Text statistics

The folder `texts/` holds three short plain-text files. For each one, count:

- its words: runs of characters separated by whitespace;
- its sentences: runs ending in `.`, `!` or `?`;
- its longest word, ignoring punctuation at either end of a word.

Write a small Python script, `scripts/count.py`, that works these out with the
standard library only, and run it. It writes two files:

1. `results/counts.json`: an object with one key per file name (for example
   `"harbour.txt"`), each holding `{"words": N, "sentences": N, "longest": "word"}`,
   and a key `"total"` holding the words and sentences of all three together.
2. `results/summary.md`: a short Markdown table of the same numbers, one row
   per file and a last row for the total, then one sentence saying which text
   is the longest.

Do not change anything in `texts/`. Do not install anything. When both
files are written, check that the numbers in them agree, and stop.
