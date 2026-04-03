"""
session.py - SQLite-backed session persistence.

Each session stores the original text, optionally rewritten text,
the sentence list, current playback index, and a generated title.
Auto-saved on any state change.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


DB_PATH = Path.home() / ".speakeasy" / "sessions.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT    NOT NULL DEFAULT '',
                original     TEXT    NOT NULL,
                rewritten    TEXT,
                sentences    TEXT    NOT NULL,  -- JSON list
                current_idx  INTEGER NOT NULL DEFAULT 0,
                voice        TEXT    NOT NULL DEFAULT '',
                speed        REAL    NOT NULL DEFAULT 1.0,
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            )
        """)


@dataclass
class Session:
    original: str
    sentences: list[str]
    rewritten: Optional[str] = None
    title: str = ""
    current_idx: int = 0
    voice: str = ""
    speed: float = 1.0
    session_id: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def save(self) -> int:
        """Insert or update the session; returns session_id."""
        now = datetime.now().isoformat()
        self.updated_at = now
        sentences_json = json.dumps(self.sentences)

        with _get_conn() as conn:
            if self.session_id is None:
                cur = conn.execute(
                    """INSERT INTO sessions
                       (title, original, rewritten, sentences,
                        current_idx, voice, speed, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        self.title, self.original, self.rewritten,
                        sentences_json, self.current_idx,
                        self.voice, self.speed,
                        self.created_at, now,
                    ),
                )
                self.session_id = cur.lastrowid
            else:
                conn.execute(
                    """UPDATE sessions SET
                       title=?, rewritten=?, sentences=?,
                       current_idx=?, updated_at=?
                       WHERE session_id=?""",
                    (
                        self.title, self.rewritten, sentences_json,
                        self.current_idx, now, self.session_id,
                    ),
                )
        return self.session_id  # type: ignore[return-value]

    def update_index(self, idx: int) -> None:
        self.current_idx = idx
        self.save()


def load_session(session_id: int) -> Optional["Session"]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
    if row is None:
        return None
    return Session(
        session_id=row["session_id"],
        title=row["title"],
        original=row["original"],
        rewritten=row["rewritten"],
        sentences=json.loads(row["sentences"]),
        current_idx=row["current_idx"],
        voice=row["voice"],
        speed=row["speed"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def delete_session(session_id: int) -> bool:
    """Delete a session by ID. Returns True if a row was deleted."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
    return cur.rowcount > 0


def list_sessions() -> list[dict]:
    from .constants import PARAGRAPH_BREAK
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT session_id, title, current_idx, sentences, created_at FROM sessions ORDER BY session_id DESC"
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        sentences = json.loads(d.pop("sentences"))
        current_idx = d["current_idx"]
        playable_total = sum(1 for s in sentences if s != PARAGRAPH_BREAK)
        playable_current = sum(1 for s in sentences[:current_idx] if s != PARAGRAPH_BREAK)
        d["playable_total"] = playable_total
        d["playable_current"] = min(playable_current, playable_total)
        result.append(d)
    return result
