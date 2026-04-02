"""
ui.py - Full-screen Rich terminal UI.

Layout (top → bottom):
  ┌──────────────────────────────────┐
  │  [status bar: title | paused?]   │
  │                                  │
  │  ... previous sentences ...      │
  │                                  │
  │  ▶  CURRENT SENTENCE (bold)      │  ← vertically centered
  │                                  │
  │  ... next sentences ...          │
  │                                  │
  │  [keybinding hint bar]           │
  └──────────────────────────────────┘

Paragraph breaks are shown as a horizontal rule.

Key capture runs in a separate thread using termios/tty so it doesn't
block the render loop.  Commands are forwarded to the PlaybackEngine
via its command queue.
"""

import os
import select
import sys
import threading
import time
import termios
import tty
from typing import Optional, Callable

from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.columns import Columns
from rich.rule import Rule
from rich.panel import Panel
from rich import box
from rich.layout import Layout
from rich.align import Align

from .player import (
    PlaybackEngine,
    CMD_PAUSE_RESUME, CMD_NEXT, CMD_PREV, CMD_QUIT,
)
from .constants import PARAGRAPH_BREAK

HINT = (
    "[dim]space[/dim] pause/resume  "
    "[dim]k[/dim] next  "
    "[dim]j[/dim] prev  "
    "[dim]q[/dim] quit"
)

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

CONTEXT_LINES = 5   # how many surrounding sentences to show on each side


class SpeakeasyUI:
    def __init__(
        self,
        sentences: list[str],
        engine: PlaybackEngine,
        title: str = "",
        on_quit: Optional[Callable[[], None]] = None,
    ):
        self.sentences = sentences
        self.engine = engine
        self.title = title
        self.on_quit = on_quit or (lambda: None)
        self._running = True
        self._console = Console()

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Start key-capture thread + Rich Live render loop."""
        key_thread = threading.Thread(target=self._key_loop, daemon=True)
        key_thread.start()

        # Render loop — refresh ~10 fps (plenty for text UI)
        with Live(
            self._render(),
            console=self._console,
            refresh_per_second=10,
            screen=True,
        ) as live:
            while self._running and not self.engine.is_stopped:
                live.update(self._render())
                time.sleep(0.1)

        self.on_quit()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _render(self):
        idx = self.engine.current_index
        paused = self.engine.is_paused

        # Build the status line
        status_parts = []
        if self.title:
            status_parts.append(f"[bold cyan]{self.title}[/bold cyan]")
        if paused:
            status_parts.append("[yellow]⏸ PAUSED[/yellow]")
        else:
            status_parts.append("[green]▶ PLAYING[/green]")
        status_parts.append(f"[dim]{idx + 1}/{len(self.sentences)}[/dim]")
        status_line = Text.from_markup("  ".join(status_parts))

        # Gather visible sentence windows
        lines = self._build_content(idx)

        from rich.console import Group
        return Panel(
            Group(
                Align.center(status_line),
                Rule(style="dim"),
                *lines,
                Rule(style="dim"),
                Align.center(Text.from_markup(HINT)),
            ),
            border_style="dim",
            box=box.ROUNDED,
            expand=True,
        )

    def _build_content(self, current_idx: int):
        """
        Return a list of Rich renderables: sentences around current_idx,
        with paragraph breaks as Rules.
        """
        from rich.console import Group

        total = len(self.sentences)
        # Determine window: CONTEXT_LINES before and after
        start = max(0, current_idx - CONTEXT_LINES)
        end = min(total, current_idx + CONTEXT_LINES + 1)

        items = []
        prev_was_para = False

        for i in range(start, end):
            sent = self.sentences[i]

            if sent == PARAGRAPH_BREAK:
                if not prev_was_para:
                    items.append(Rule(style="dim blue"))
                prev_was_para = True
                continue
            prev_was_para = False

            if i == current_idx:
                # Highlighted current sentence
                t = Text(f"▶  {sent}", style="bold bright_white on dark_blue")
                items.append(Align.left(t))
            elif i < current_idx:
                items.append(Align.left(Text(sent, style="dim white")))
            else:
                if not self.engine.is_ready(i):
                    frame = int(time.time() * 10) % len(SPINNER_FRAMES)
                    spinner = SPINNER_FRAMES[frame]
                    items.append(Align.left(Text(f"{spinner}  {sent}", style="dim cyan")))
                else:
                    items.append(Align.left(Text(sent, style="white")))

            items.append(Text(""))  # blank line between sentences

        return items

    # ------------------------------------------------------------------ #
    # Key capture
    # ------------------------------------------------------------------ #

    def _key_loop(self) -> None:
        """
        Read single keypresses from the terminal in raw mode.

        Opens /dev/tty directly so this works even when stdin is a pipe
        (e.g. `cat file.txt | speakeasy start`).  Uses select() with a
        timeout so the loop can exit cleanly when _running becomes False.
        Uses os.read() to bypass Python's buffered I/O layer.
        """
        try:
            # Open the actual terminal regardless of stdin redirection
            tty_fd = os.open("/dev/tty", os.O_RDONLY)
        except OSError:
            tty_fd = sys.stdin.fileno()

        old_settings = termios.tcgetattr(tty_fd)
        try:
            tty.setcbreak(tty_fd)
            while self._running and not self.engine.is_stopped:
                # 100 ms timeout so we re-check _running regularly
                readable, _, _ = select.select([tty_fd], [], [], 0.1)
                if readable:
                    raw = os.read(tty_fd, 1)
                    ch = raw.decode("utf-8", errors="replace")
                    self._dispatch_key(ch)
        except Exception:
            pass
        finally:
            termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_settings)
            if tty_fd != sys.stdin.fileno():
                os.close(tty_fd)

    def _dispatch_key(self, ch: str) -> None:
        if ch in (" ",):
            self.engine.send_command(CMD_PAUSE_RESUME)
        elif ch in ("k", "K"):
            self.engine.send_command(CMD_NEXT)
        elif ch in ("j", "J"):
            self.engine.send_command(CMD_PREV)
        elif ch in ("q", "Q", "\x03"):  # q, Q, or Ctrl-C
            self._running = False
            self.engine.send_command(CMD_QUIT)
