"""
tts.py - Piper TTS integration.

Piper is invoked as a subprocess (piper CLI) rather than via the Python
library to avoid loading the model into the main process and blocking
the event loop.  The generated WAV is written to the cache path.

Usage:
    synthesize(text, out_path, voice_path, speed)

Voice model files must be downloaded separately:
    mkdir -p ~/.local/share/piper
    cd ~/.local/share/piper
    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
"""

import subprocess
import shutil
import sys
from pathlib import Path
from typing import Optional


DEFAULT_VOICE_DIR = Path.home() / ".local" / "share" / "piper"
DEFAULT_VOICE_NAME = "en_US-lessac-medium"


def default_voice_path() -> Optional[Path]:
    """Return the .onnx model path if it exists, else None."""
    candidate = DEFAULT_VOICE_DIR / f"{DEFAULT_VOICE_NAME}.onnx"
    return candidate if candidate.exists() else None


def synthesize(
    text: str,
    out_path: Path,
    voice_path: Optional[Path] = None,
    speed: float = 1.0,
) -> bool:
    """
    Run Piper TTS to generate a WAV file at out_path.

    Returns True on success, False on failure.

    Piper reads text from stdin and writes a WAV to --output_file.
    The --length_scale parameter controls speed:
        length_scale < 1 → faster, > 1 → slower
    So speed=1.5 means 1.5x faster → length_scale = 1/1.5 ≈ 0.667
    """
    piper_bin = shutil.which("piper") or shutil.which("piper-tts")
    if piper_bin is None:
        # When installed as a uv tool, binaries share the same env bin dir
        # as the Python interpreter — check there first, then common locations.
        env_bin = Path(sys.executable).parent
        candidates = [
            env_bin / "piper",
            env_bin / "piper-tts",
            Path.home() / ".local" / "bin" / "piper",
            Path.home() / ".local" / "bin" / "piper-tts",
        ]
        for c in candidates:
            if c.exists():
                piper_bin = str(c)
                break

    if piper_bin is None:
        raise RuntimeError(
            "Piper binary not found. Install with: pip install piper-tts\n"
            "Then ensure the binary is on your PATH."
        )

    if voice_path is None:
        voice_path = default_voice_path()
    if voice_path is None:
        raise RuntimeError(
            f"Piper voice model not found at {DEFAULT_VOICE_DIR}.\n"
            "See instructions in tts.py or the README to download."
        )

    # length_scale inverts speed: 1/speed makes speech faster when speed>1
    length_scale = 1.0 / max(speed, 0.1)

    cmd = [
        piper_bin,
        "--model", str(voice_path),
        "--output_file", str(out_path),
        "--length_scale", f"{length_scale:.4f}",
        "--sentence_silence", "0.3",   # short pause between sentences
    ]

    try:
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False
        return out_path.exists() and out_path.stat().st_size > 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
