"""
player.py - Playback engine with sentence-level controls.

Architecture:
- PlaybackEngine runs in its own thread, playing WAV files via sounddevice.
- A background GeneratorThread pre-generates audio for upcoming sentences.
- State (current index, paused, stopped) is protected by a threading.Lock.
- Key events are passed via a queue from the UI to the engine.

Keybindings (handled in ui.py, sent here as commands):
    space  → pause / resume
    j      → previous sentence
    k      → next sentence
    q      → quit
"""

import subprocess
import sys
import threading
import queue
import time
from pathlib import Path
from typing import Callable, Optional

import sounddevice as sd
import soundfile as sf

from .cache import cache_path, is_cached
from .tts import synthesize
from .constants import PARAGRAPH_BREAK


# Commands sent from UI → engine
CMD_PAUSE_RESUME = "pause_resume"
CMD_NEXT = "next"
CMD_PREV = "prev"
CMD_QUIT = "quit"
CMD_JUMP = "jump"  # payload: sentence index


class PlaybackEngine:
    """
    Drives sentence-by-sentence playback with background TTS generation.

    Callbacks:
        on_sentence_change(idx)  → called whenever the active sentence changes
        on_state_change()        → called when paused/stopped state changes
    """

    def __init__(
        self,
        sentences: list[str],
        voice: str,
        speed: float,
        start_index: int = 0,
        start_paused: bool = False,
        on_sentence_change: Optional[Callable[[int], None]] = None,
        on_state_change: Optional[Callable[[], None]] = None,
    ):
        self.sentences = sentences
        self.voice = voice
        self.speed = speed

        self._idx = start_index
        self._paused = start_paused
        self._stopped = False

        self._lock = threading.Lock()
        self._cmd_queue: queue.Queue[tuple] = queue.Queue()
        self._playback_done = threading.Event()

        self.on_sentence_change = on_sentence_change or (lambda i: None)
        self.on_state_change = on_state_change or (lambda: None)

        # Tracks cache paths already queued for generation (keyed by path, not
        # index, so duplicate sentence text doesn't spawn two concurrent writes
        # to the same file).
        self._gen_requested: set[Path] = set()
        self._gen_lock = threading.Lock()

        # Cancel events for in-flight generation threads, keyed by cache path.
        self._cancel_events: dict[Path, threading.Event] = {}
        # Debounce timer: fires _request_generation_ahead after a short pause.
        self._gen_timer: Optional[threading.Timer] = None

    # ------------------------------------------------------------------ #
    # Public API (thread-safe)
    # ------------------------------------------------------------------ #

    @property
    def current_index(self) -> int:
        with self._lock:
            return self._idx

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def is_stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def send_command(self, cmd: str, payload=None) -> None:
        self._cmd_queue.put((cmd, payload))

    def run(self) -> None:
        """Main loop — call from a dedicated thread."""
        self._request_generation_ahead()

        while True:
            with self._lock:
                if self._stopped:
                    break
                idx = self._idx
                paused = self._paused
                if idx >= len(self.sentences):
                    self._stopped = True
                    break

            # --- Process any pending commands (non-blocking) ---
            try:
                cmd, payload = self._cmd_queue.get_nowait()
                self._handle_command(cmd, payload)
                continue
            except queue.Empty:
                pass

            if paused:
                time.sleep(0.05)
                continue

            # --- Skip paragraph-break sentinels silently ---
            if self.sentences[idx] == PARAGRAPH_BREAK:
                with self._lock:
                    next_idx = self._idx + 1
                    if next_idx >= len(self.sentences):
                        self._stopped = True
                        break
                    self._idx = next_idx
                    self.on_sentence_change(self._idx)
                continue

            # --- Play the current sentence ---
            audio_path = cache_path(self.sentences[idx], self.voice, self.speed)
            if not is_cached(self.sentences[idx], self.voice, self.speed):
                # Generation not ready yet — wait briefly
                time.sleep(0.1)
                continue

            interrupted = self._play_file(audio_path)

            if interrupted:
                # A command arrived mid-playback; loop to process it
                continue

            # Natural end of sentence → advance
            _advanced = False
            with self._lock:
                if not self._paused and not self._stopped:
                    next_idx = self._idx + 1
                    if next_idx >= len(self.sentences):
                        self._idx = next_idx  # mark fully complete → 100%
                        self._stopped = True
                        break
                    self._idx = next_idx
                    self.on_sentence_change(self._idx)
                    _advanced = True
            if _advanced:
                self._request_generation_ahead()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
        self._interrupt_playback()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _skip_breaks(self, idx: int, direction: int) -> int:
        """Advance idx in direction (+1/-1) until it points to a non-break sentence."""
        while 0 < idx < len(self.sentences) - 1 and self.sentences[idx] == PARAGRAPH_BREAK:
            idx += direction
        return idx

    def _handle_command(self, cmd: str, payload) -> None:
        _do_gen = False
        _debounce = False
        with self._lock:
            if cmd == CMD_PAUSE_RESUME:
                self._paused = not self._paused
                if self._paused:
                    self._interrupt_playback()
                self.on_state_change()

            elif cmd == CMD_NEXT:
                new_idx = self._skip_breaks(min(self._idx + 1, len(self.sentences) - 1), +1)
                self._idx = new_idx
                self._interrupt_playback()
                self.on_sentence_change(self._idx)
                _do_gen = True
                _debounce = True

            elif cmd == CMD_PREV:
                new_idx = self._skip_breaks(max(self._idx - 1, 0), -1)
                self._idx = new_idx
                self._interrupt_playback()
                self.on_sentence_change(self._idx)
                _do_gen = True
                _debounce = True

            elif cmd == CMD_JUMP:
                new_idx = self._skip_breaks(max(0, min(int(payload), len(self.sentences) - 1)), +1)
                self._idx = new_idx
                self._interrupt_playback()
                self.on_sentence_change(self._idx)
                _do_gen = True
                _debounce = True

            elif cmd == CMD_QUIT:
                self._stopped = True
                self._interrupt_playback()

        if _do_gen:
            if _debounce:
                self._schedule_generation()
            else:
                self._request_generation_ahead()

    def _interrupt_playback(self) -> None:
        """Stop PortAudio playback immediately."""
        try:
            sd.stop()
        except Exception:
            pass

    def _play_file(self, path: Path) -> bool:
        """
        Play a WAV file, returning True if interrupted by a command.

        On macOS uses afplay (out-of-process, immune to Python CPU contention).
        On other platforms falls back to sounddevice.
        """
        if sys.platform == "darwin":
            return self._play_file_afplay(path)
        return self._play_file_sd(path)

    def _play_file_afplay(self, path: Path) -> bool:
        try:
            proc = subprocess.Popen(
                ["afplay", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return False

        while proc.poll() is None:
            if not self._cmd_queue.empty():
                proc.kill()
                proc.wait()
                return True
            time.sleep(0.01)

        # One final check: a command may have arrived the moment afplay finished
        return not self._cmd_queue.empty()

    def _play_file_sd(self, path: Path) -> bool:
        try:
            data, samplerate = sf.read(str(path), dtype="float32")
        except Exception:
            return False

        sd.play(data, samplerate=samplerate, latency="high")

        wait_done = threading.Event()

        def _wait():
            try:
                sd.wait()
            except Exception:
                pass
            finally:
                wait_done.set()

        t = threading.Thread(target=_wait, daemon=True)
        t.start()

        while not wait_done.wait(timeout=0.05):
            if not self._cmd_queue.empty():
                sd.stop()
                t.join(timeout=0.5)
                return True

        # One final check: a command may have arrived the moment playback finished
        return not self._cmd_queue.empty()

    def is_ready(self, idx: int) -> bool:
        """Return True if the sentence at idx is cached and ready to play."""
        if idx < 0 or idx >= len(self.sentences):
            return True
        sent = self.sentences[idx]
        if sent == PARAGRAPH_BREAK:
            return True
        return is_cached(sent, self.voice, self.speed)

    def _schedule_generation(self) -> None:
        """Debounce TTS generation: wait 250 ms after the last nav command."""
        with self._gen_lock:
            if self._gen_timer is not None:
                self._gen_timer.cancel()
            t = threading.Timer(0.25, self._request_generation_ahead)
            t.daemon = True
            self._gen_timer = t
        t.start()

    def _request_generation_ahead(self) -> None:
        """
        Kick off background TTS generation for current + next 5 sentences.
        Cancels in-flight generation for sentences no longer in the window.
        Skips sentences already cached or already being generated.
        """
        with self._gen_lock:
            self._gen_timer = None

        with self._lock:
            base = self._idx

        # Build the desired lookahead window: (sent_idx, text, dest)
        lookahead: list[tuple[int, str, Path]] = []
        generated = 0
        idx = base
        while generated < 6 and idx < len(self.sentences):
            text = self.sentences[idx]
            if text != PARAGRAPH_BREAK:
                generated += 1
                dest = cache_path(text, self.voice, self.speed)
                if not (dest.exists() and dest.stat().st_size > 0):
                    lookahead.append((idx, text, dest))
            idx += 1

        wanted = {dest for _, _, dest in lookahead}

        # Cancel in-flight procs that are no longer in the window.
        with self._gen_lock:
            for path in list(self._cancel_events):
                if path not in wanted:
                    self._cancel_events[path].set()
                    del self._cancel_events[path]
                    self._gen_requested.discard(path)

        # Spawn threads for paths not already generating.
        for sent_idx, text, dest in lookahead:
            with self._gen_lock:
                if dest in self._gen_requested:
                    continue
                self._gen_requested.add(dest)
                cancel = threading.Event()
                self._cancel_events[dest] = cancel

            t = threading.Thread(
                target=self._generate_sentence,
                args=(sent_idx, cancel),
                daemon=True,
                name=f"gen-{sent_idx}",
            )
            t.start()

    def _generate_sentence(self, idx: int, cancel_event: threading.Event) -> None:
        """Generate TTS for sentences[idx] and write atomically to cache."""
        text = self.sentences[idx]
        if text == PARAGRAPH_BREAK:
            return
        dest = cache_path(text, self.voice, self.speed)
        if dest.exists() and dest.stat().st_size > 0:
            return
        voice_path = Path(self.voice) if self.voice else None
        # Write to a sibling temp file then rename so the player never sees a
        # partial write — even if two threads somehow target the same path.
        tmp = dest.with_suffix(".tmp")
        try:
            ok = synthesize(
                text, tmp,
                voice_path=voice_path,
                speed=self.speed,
                cancel_event=cancel_event,
            )
            if ok and tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(dest)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            with self._gen_lock:
                self._cancel_events.pop(dest, None)
