"""
rewrite.py - Ollama-based text rewriting and title generation.

Calls the local Ollama HTTP API (default: http://localhost:11434).
Falls back gracefully if Ollama is unavailable.
"""

import json
import requests
from typing import Callable, Optional


OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3:8b"
TIMEOUT = 300  # seconds — model load can be slow on first run


def _ollama_generate(
    prompt: str,
    model: str = DEFAULT_MODEL,
    on_token: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Call Ollama and return (response_text, error_message).
    If on_token is provided, streams and calls it with each token as it arrives.
    """
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": on_token is not None},
            timeout=TIMEOUT,
            stream=on_token is not None,
        )
        resp.raise_for_status()

        if on_token is not None:
            parts = []
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get("response", "")
                if token:
                    parts.append(token)
                    on_token(token)
                if chunk.get("done"):
                    break
            return "".join(parts).strip(), None
        else:
            return resp.json().get("response", "").strip(), None

    except requests.exceptions.ConnectionError:
        return None, "Ollama not running (connection refused)"
    except requests.exceptions.Timeout:
        return None, f"Ollama timed out after {TIMEOUT}s"
    except Exception as e:
        return None, str(e)


REWRITE_PROMPT = """\
You are an editor preparing text for text-to-speech audio. Rewrite the following text:

REMOVE completely:
- Any sentence that is meta-commentary, transition filler, or structural signposting \
("in this chapter", "as we discussed", "it is worth noting", "in conclusion", "to summarize", etc.)
- Redundant restatements and padding
- Introductory throat-clearing

CONVERT:
- Bullet points, numbered lists, and any list formatting into plain prose sentences \
(e.g. "There are three options: X, Y, and Z.")

FORMAT RULES — these are strict:
- Plain text only. No asterisks, no hyphens as bullets, no pound signs, no markdown of any kind.
- Only use: letters, numbers, spaces, commas, periods, colons, semicolons, \
question marks, exclamation marks, and parentheses.
- Preserve paragraph breaks.

Return ONLY the rewritten text. No preamble, no explanation, no commentary.

TEXT:
{text}
"""

TITLE_PROMPT = """\
Generate a short, descriptive title (3–8 words) for the following text.
Return ONLY the title, nothing else.

TEXT:
{text}
"""


_PREAMBLE_PREFIXES = (
    "here is the rewritten text:",
    "here is the rewritten version:",
    "here is a rewritten version:",
    "here's the rewritten text:",
    "here's the rewritten version:",
    "rewritten text:",
    "rewritten version:",
)


def _strip_preamble(text: str) -> str:
    """Remove common model preamble lines that precede the actual content."""
    lines = text.splitlines()
    while lines:
        if lines[0].strip().lower().rstrip(".") in _PREAMBLE_PREFIXES:
            lines = lines[1:]
            # drop any blank lines that follow
            while lines and not lines[0].strip():
                lines = lines[1:]
        else:
            break
    return "\n".join(lines).strip()


def rewrite_text(
    text: str,
    model: str = DEFAULT_MODEL,
    on_token: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Rewrite text for clarity.
    Returns (rewritten_text, None) on success, (None, error_message) on failure.
    Pass on_token to receive streaming updates.
    """
    result, err = _ollama_generate(REWRITE_PROMPT.format(text=text), model=model, on_token=on_token)
    if result is not None:
        result = _strip_preamble(result)
    return result, err


def generate_title(text: str, model: str = DEFAULT_MODEL) -> str:
    """Generate a 3-8 word title; falls back to a truncated first sentence."""
    snippet = text[:500].replace("\n", " ")
    result, _ = _ollama_generate(TITLE_PROMPT.format(text=snippet), model=model)
    if result is None:
        words = text.split()
        return " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return result.strip('"\'').strip()
