"""
cache.py - Audio file caching for speakeasy.

Audio is keyed by SHA-256 hash of (sentence_text + voice_name + speed).
This means identical sentences reuse audio across sessions.
"""

import hashlib
import os
from pathlib import Path


CACHE_DIR = Path.home() / ".speakeasy" / "cache"


def ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def cache_key(text: str, voice: str, speed: float) -> str:
    """Stable hash for a (text, voice, speed) triple."""
    payload = f"{text}||{voice}||{speed:.3f}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def cache_path(text: str, voice: str, speed: float) -> Path:
    key = cache_key(text, voice, speed)
    return ensure_cache_dir() / f"{key}.wav"


def is_cached(text: str, voice: str, speed: float) -> bool:
    p = cache_path(text, voice, speed)
    return p.exists() and p.stat().st_size > 0
