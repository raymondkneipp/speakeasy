"""
splitter.py - Sentence splitting with paragraph preservation.

Uses NLTK's Punkt tokenizer.  Paragraph breaks (double newlines) are
preserved by inserting a PARAGRAPH_BREAK sentinel into the sentence list.
The UI renders these as horizontal rules.
"""

import re
from typing import Optional

try:
    import nltk
    from nltk.tokenize import sent_tokenize
    _NLTK_AVAILABLE = True
except ImportError:
    _NLTK_AVAILABLE = False

from .constants import PARAGRAPH_BREAK


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


def split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences, inserting PARAGRAPH_BREAK between paragraphs.

    Paragraphs are separated by one or more blank lines.
    Empty paragraphs and whitespace-only entries are discarded.
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

        if _NLTK_AVAILABLE:
            sents = sent_tokenize(para)
        else:
            # Naive fallback: split on ". ", "! ", "? "
            sents = re.split(r"(?<=[.!?])\s+", para)

        for s in sents:
            s = s.strip()
            if s:
                result.append(s)

    return result
