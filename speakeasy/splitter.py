"""
splitter.py - Sentence splitting with paragraph preservation.

Uses NLTK's Punkt tokenizer.  Paragraph breaks (double newlines) are
preserved by inserting a PARAGRAPH_BREAK sentinel into the sentence list.
The UI renders these as horizontal rules.

Long sentences (no periods, bullet-style text, etc.) are broken further
at semicolons, em-dashes, commas, or hard word-count limits.
"""

import re

try:
    import nltk
    from nltk.tokenize import sent_tokenize
    _NLTK_AVAILABLE = True
except ImportError:
    _NLTK_AVAILABLE = False

from .constants import PARAGRAPH_BREAK


MAX_SENTENCE_WORDS = 50  # break any sentence longer than this


def _ensure_punkt() -> None:
    """Download Punkt tokenizer data if not present (silent)."""
    if not _NLTK_AVAILABLE:
        return
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        try:
            nltk.download("punkt", quiet=True)
        except Exception:
            pass
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        try:
            nltk.download("punkt_tab", quiet=True)
        except Exception:
            pass


def _chunk_long(text: str, max_words: int = MAX_SENTENCE_WORDS) -> list[str]:
    """
    Recursively break a too-long string at natural boundaries.
    Tries semicolons → em-dashes → commas → hard word-count split.
    """
    if len(text.split()) <= max_words:
        return [text]

    for pattern, joiner in [
        (r"\s*;\s*", "; "),
        (r"\s*[—–]\s*", " — "),
        (r"\s*,\s*", ", "),
    ]:
        parts = [p.strip() for p in re.split(pattern, text) if p.strip()]
        if len(parts) < 2:
            continue

        # Merge consecutive short parts so we don't produce single-word chunks
        merged: list[str] = []
        buf: list[str] = []
        buf_wc = 0
        for p in parts:
            wc = len(p.split())
            if buf and buf_wc + wc > max_words:
                merged.append(joiner.join(buf))
                buf, buf_wc = [p], wc
            else:
                buf.append(p)
                buf_wc += wc
        if buf:
            merged.append(joiner.join(buf))

        result: list[str] = []
        for m in merged:
            result.extend(_chunk_long(m, max_words))
        return result

    # Last resort: hard split at word boundaries
    words = text.split()
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences, inserting PARAGRAPH_BREAK between paragraphs.

    Paragraphs are separated by one or more blank lines.
    Empty paragraphs and whitespace-only entries are discarded.
    Sentences longer than MAX_SENTENCE_WORDS words are broken further.
    """
    _ensure_punkt()

    # Normalize line endings and split on blank lines
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n{2,}", text)

    result: list[str] = []
    first = True

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if not first:
            result.append(PARAGRAPH_BREAK)
        first = False

        # Split on single newlines within the paragraph first — many plain-text
        # formats (exports, notes) use single newlines as item separators
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]

        for line in lines:
            if _NLTK_AVAILABLE:
                sents = sent_tokenize(line)
            else:
                sents = re.split(r"(?<=[.!?])\s+", line)

            for s in sents:
                s = s.strip()
                if not s:
                    continue
                for chunk in _chunk_long(s):
                    if chunk:
                        result.append(chunk)

    return result
