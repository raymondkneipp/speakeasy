"""
main.py - CLI entrypoint for speakeasy.

Commands:
    speakeasy start  [--text TEXT | --file FILE | stdin]
                     [--rewrite] [--speed FLOAT] [--voice PATH]
    speakeasy list
    speakeasy resume <id>
"""

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich import box

from .session import Session, init_db, list_sessions, load_session, delete_session
from .cache import CACHE_DIR
from .rewrite import rewrite_text
from .splitter import split_into_sentences
from .tts import default_voice_path
from .player import PlaybackEngine
from .ui import SpeakeasyUI
from .constants import PARAGRAPH_BREAK

console = Console()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _read_input(args) -> Optional[str]:
    """Resolve text from --text, --file, or stdin."""
    if hasattr(args, "text") and args.text:
        return args.text
    if hasattr(args, "file") and args.file:
        p = Path(args.file)
        if not p.exists():
            console.print(f"[red]File not found:[/red] {args.file}")
            sys.exit(1)
        return p.read_text(encoding="utf-8")
    # stdin (pipe or interactive)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


def _resolve_voice(voice_arg: Optional[str]) -> str:
    """Return voice path string (may be empty → tts.py uses default)."""
    if voice_arg:
        return voice_arg
    p = default_voice_path()
    return str(p) if p else ""


def _playable_sentences(sentences: list[str]) -> list[str]:
    """Return only non-paragraph-break sentences for index calculations."""
    return [s for s in sentences if s != PARAGRAPH_BREAK]


# ------------------------------------------------------------------ #
# Command: start
# ------------------------------------------------------------------ #

def _run_debug(sentences: list[str], voice: str, speed: float) -> None:
    """Simple debug runner: print each sentence and speak it, no UI."""
    import sounddevice as sd
    import soundfile as sf
    from .tts import synthesize
    from .cache import cache_path, is_cached
    from .constants import PARAGRAPH_BREAK

    playable = [s for s in sentences if s != PARAGRAPH_BREAK]
    console.print(f"\n[bold cyan]--- DEBUG MODE ---[/bold cyan]")
    console.print(f"[dim]Voice:[/dim] {voice or '(default)'}")
    console.print(f"[dim]Speed:[/dim] {speed}")
    console.print(f"[dim]Sentences:[/dim] {len(playable)}\n")

    for i, sentence in enumerate(sentences):
        if sentence == PARAGRAPH_BREAK:
            console.print("[dim]--- paragraph break ---[/dim]")
            continue

        console.print(f"[bold][{i+1}/{len(playable)}][/bold] {sentence}")

        out_path = cache_path(sentence, voice, speed)

        if is_cached(sentence, voice, speed):
            console.print(f"  [dim]cache hit → {out_path.name}[/dim]")
        else:
            console.print(f"  [dim]synthesizing → {out_path.name}[/dim]")
            try:
                ok = synthesize(sentence, out_path, Path(voice) if voice else None, speed)
            except RuntimeError as e:
                console.print(f"  [red]TTS error: {e}[/red]")
                break
            if not ok:
                console.print(f"  [red]TTS failed — skipping[/red]")
                continue
            console.print(f"  [green]synthesized ({out_path.stat().st_size} bytes)[/green]")

        try:
            console.print(f"  [dim]playing...[/dim]")
            import subprocess as _sp
            result = _sp.run(["afplay", str(out_path)], capture_output=True)
            if result.returncode == 0:
                console.print(f"  [green]done[/green]")
            else:
                console.print(f"  [red]afplay error: {result.stderr.decode().strip()}[/red]")
        except Exception as e:
            console.print(f"  [red]playback error: {e}[/red]")

    console.print("\n[bold cyan]--- done ---[/bold cyan]")


def cmd_start(args) -> None:
    raw_text = _read_input(args)
    if not raw_text or not raw_text.strip():
        console.print("[red]No input text provided.[/red]")
        console.print("Use: speakeasy start --text 'hello' OR pipe text in.")
        sys.exit(1)

    voice = _resolve_voice(args.voice)
    speed = args.speed

    # --- Rewrite ---
    if args.rewrite:
        token_count = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[dim]Rewriting…[/dim]  [cyan]{task.fields[tokens]}[/cyan][dim] tokens[/dim]"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("rewrite", total=None, tokens=0)

            def _on_token(t: str) -> None:
                nonlocal token_count
                token_count += 1
                progress.update(task, tokens=token_count)

            rewritten, err = rewrite_text(raw_text, on_token=_on_token)

        if rewritten is None:
            console.print(f"[yellow]Rewrite failed: {err}. Using original text.[/yellow]")
            rewritten = None
            active_text = raw_text
        else:
            console.print(f"[dim]Rewrite done ({token_count} tokens).[/dim]")
            active_text = rewritten
    else:
        rewritten = None
        active_text = raw_text

    # --- Split into sentences ---
    sentences = split_into_sentences(active_text)
    if not sentences:
        console.print("[red]No sentences found in input.[/red]")
        sys.exit(1)

    # --- Derive title from first sentence ---
    first = next((s for s in sentences if s), "")
    words = first.split()
    title = " ".join(words[:8]) + ("…" if len(words) > 8 else "")

    # --- Create and save session ---
    init_db()
    session = Session(
        original=raw_text,
        rewritten=rewritten,
        sentences=sentences,
        voice=voice,
        speed=speed,
        title=title,
    )
    session.save()

    if args.debug:
        _run_debug(sentences, voice, speed)
    else:
        _run_session(session, start_paused=args.rewrite)


def cmd_load(args) -> None:
    raw_text = _read_input(args)
    if not raw_text or not raw_text.strip():
        console.print("[red]No input text provided.[/red]")
        console.print("Use: speakeasy load --file notes.txt OR pipe text in.")
        sys.exit(1)

    voice = _resolve_voice(args.voice)
    speed = args.speed

    # --- Rewrite ---
    if args.rewrite:
        token_count = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[dim]Rewriting…[/dim]  [cyan]{task.fields[tokens]}[/cyan][dim] tokens[/dim]"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("rewrite", total=None, tokens=0)

            def _on_token(t: str) -> None:
                nonlocal token_count
                token_count += 1
                progress.update(task, tokens=token_count)

            rewritten, err = rewrite_text(raw_text, on_token=_on_token)

        if rewritten is None:
            console.print(f"[yellow]Rewrite failed: {err}. Using original text.[/yellow]")
            rewritten = None
            active_text = raw_text
        else:
            console.print(f"[dim]Rewrite done ({token_count} tokens).[/dim]")
            active_text = rewritten
    else:
        rewritten = None
        active_text = raw_text

    # --- Split into sentences ---
    sentences = split_into_sentences(active_text)
    if not sentences:
        console.print("[red]No sentences found in input.[/red]")
        sys.exit(1)

    # --- Derive title from first sentence ---
    first = next((s for s in sentences if s), "")
    words = first.split()
    title = " ".join(words[:8]) + ("…" if len(words) > 8 else "")

    # --- Save session ---
    init_db()
    session = Session(
        original=raw_text,
        rewritten=rewritten,
        sentences=sentences,
        voice=voice,
        speed=speed,
        title=title,
    )
    session.save()

    console.print(f"[dim]Loaded[/dim] [bold]{title}[/bold] [dim]({len(sentences)} sentences, id={session.session_id})[/dim]")


# ------------------------------------------------------------------ #
# Command: list
# ------------------------------------------------------------------ #

def _progress_bar(current: int, total: int, width: int = 20) -> str:
    """Return a simple ASCII progress bar string."""
    if total == 0:
        return "[dim]──────────────────── 0%[/dim]"
    pct = current / total
    filled = round(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[cyan]{bar}[/cyan] [dim]{round(pct * 100)}%[/dim]"


def cmd_list(args) -> None:
    init_db()
    rows = list_sessions()
    if not rows:
        console.print("[dim]No saved sessions.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Title")
    table.add_column("Progress", no_wrap=True)
    table.add_column("Created", style="dim")

    for r in rows:
        total = r["playable_total"] or 0
        current = r["playable_current"] or 0
        table.add_row(
            str(r["session_id"]),
            r["title"] or "[dim]—[/dim]",
            _progress_bar(current, total),
            r["created_at"][:16].replace("T", " "),
        )

    console.print(table)


# ------------------------------------------------------------------ #
# Command: resume
# ------------------------------------------------------------------ #

def cmd_cache(args) -> None:
    if not CACHE_DIR.exists():
        console.print("[dim]Cache is empty.[/dim]")
        return

    files = sorted(CACHE_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        console.print("[dim]Cache is empty.[/dim]")
        return

    total_bytes = sum(f.stat().st_size for f in files)

    if args.clear:
        for f in files:
            f.unlink()
        console.print(f"[dim]Cleared {len(files)} files ({_fmt_size(total_bytes)}).[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("File", style="dim")
    table.add_column("Size", justify="right")

    for f in files:
        table.add_row(f.name, _fmt_size(f.stat().st_size))

    console.print(table)
    console.print(f"\n[dim]{len(files)} files  •  {_fmt_size(total_bytes)} total  •  {CACHE_DIR}[/dim]")


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def cmd_delete(args) -> None:
    init_db()
    for session_id in args.ids:
        if delete_session(session_id):
            console.print(f"[dim]Deleted session {session_id}.[/dim]")
        else:
            console.print(f"[yellow]Session {session_id} not found.[/yellow]")


def cmd_resume(args) -> None:
    init_db()
    session = load_session(args.id)
    if session is None:
        console.print(f"[red]Session {args.id} not found.[/red]")
        sys.exit(1)

    if args.speed is not None:
        session.speed = args.speed

    console.print(f"[dim]Resuming:[/dim] [bold]{session.title}[/bold] "
                  f"(sentence {session.current_idx + 1}/{len(session.sentences)})")
    _run_session(session)


# ------------------------------------------------------------------ #
# Shared playback runner
# ------------------------------------------------------------------ #

def _run_session(session: Session, start_paused: bool = False) -> None:
    """Start the PlaybackEngine + UI for a session, with signal handling."""

    # Filter playable sentences (skip PARAGRAPH_BREAK tokens)
    # Engine works on the full list; it will skip non-audio entries.
    sentences = session.sentences
    voice = session.voice
    speed = session.speed
    # If session was completed, restart from beginning
    start_idx = session.current_idx if session.current_idx < len(session.sentences) else 0

    # --- Engine ---
    engine = PlaybackEngine(
        sentences=sentences,
        voice=voice,
        speed=speed,
        start_index=start_idx,
        start_paused=start_paused,
        on_sentence_change=lambda i: session.update_index(i),
    )

    def _save_and_exit():
        session.update_index(engine.current_index)

    # Catch Ctrl-C / SIGTERM for graceful shutdown
    def _signal_handler(sig, frame):
        engine.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # --- Engine thread ---
    engine_thread = threading.Thread(target=engine.run, daemon=True, name="engine")
    engine_thread.start()

    # --- UI (blocks until quit) ---
    ui = SpeakeasyUI(
        sentences=sentences,
        engine=engine,
        title=session.title,
        on_quit=_save_and_exit,
    )
    ui.run()

    # Ensure engine is fully stopped
    engine.stop()
    engine_thread.join(timeout=3.0)

    console.print(f"\n[dim]Session saved (id={session.session_id}).[/dim]")


# ------------------------------------------------------------------ #
# Argument parsing
# ------------------------------------------------------------------ #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speakeasy",
        description="Local-first AI text-to-speech CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- start ---
    p_start = sub.add_parser("start", help="Start a new TTS session")
    p_start.add_argument("--text", "-t", help="Text to speak")
    p_start.add_argument("--file", "-f", help="Path to text file")
    p_start.add_argument(
        "--rewrite", "-r",
        action="store_true",
        help="Rewrite text with Ollama before speaking",
    )
    p_start.add_argument(
        "--speed", "-s",
        type=float,
        default=1.0,
        metavar="FLOAT",
        help="Playback speed multiplier (default 1.0)",
    )
    p_start.add_argument(
        "--voice", "-v",
        help="Path to Piper .onnx voice model",
    )
    p_start.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Debug mode: print each sentence and speak it directly, no UI",
    )

    # --- load ---
    p_load = sub.add_parser("load", help="Save text as a session without playing")
    p_load.add_argument("--text", "-t", help="Text to load")
    p_load.add_argument("--file", "-f", help="Path to text file")
    p_load.add_argument("--rewrite", "-r", action="store_true", help="Rewrite with Ollama before saving")
    p_load.add_argument("--speed", "-s", type=float, default=1.0, metavar="FLOAT", help="Playback speed (default 1.0)")
    p_load.add_argument("--voice", "-v", help="Path to Piper .onnx voice model")

    # --- list ---
    sub.add_parser("list", help="List saved sessions")

    # --- cache ---
    p_cache = sub.add_parser("cache", help="Show or clear the audio cache")
    p_cache.add_argument("--clear", action="store_true", help="Delete all cached audio files")

    # --- delete ---
    p_delete = sub.add_parser("delete", help="Delete one or more sessions")
    p_delete.add_argument("ids", type=int, nargs="+", metavar="ID", help="Session ID(s) to delete")

    # --- resume ---
    p_resume = sub.add_parser("resume", help="Resume a previous session")
    p_resume.add_argument("id", type=int, help="Session ID")
    p_resume.add_argument(
        "--speed", "-s",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Override playback speed (default: use session speed)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "load":
        cmd_load(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "cache":
        cmd_cache(args)
    elif args.command == "delete":
        cmd_delete(args)
    elif args.command == "resume":
        cmd_resume(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
