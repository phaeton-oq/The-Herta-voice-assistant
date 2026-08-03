"""Хранилище переписок Telegram: SQLite вместо памяти процесса.

До этого история жила только в оперативной памяти и обнулялась при каждом
перезапуске моста. Здесь она переживает рестарт, но остаётся отдельной от
личной памяти владельца (data/dialogue_memory.json и long_memory.json):
чужие разговоры туда не попадают.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);

CREATE TABLE IF NOT EXISTS chats (
    chat_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    turns      INTEGER NOT NULL DEFAULT 0
);
"""

VALID_ROLES: Final[frozenset[str]] = frozenset({'user', 'assistant', 'system'})


@dataclass(slots=True)
class ChatStats:
    chat_id: int
    username: str | None
    turns: int
    stored_messages: int


class TelegramStore:
    """Потокобезопасное хранилище истории чатов."""

    def __init__(self, path: str | Path, *, max_messages_per_chat: int = 200) -> None:
        self.path = Path(path)
        self.max_messages_per_chat = max_messages_per_chat
        self._lock = threading.Lock()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    # ---------- Запись ----------

    def append(self, chat_id: int, role: str, content: str, *, timestamp: float) -> None:
        if role not in VALID_ROLES or not content.strip():
            return

        with self._lock:
            self._connection.execute(
                'INSERT INTO messages (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)',
                (chat_id, role, content.strip(), timestamp),
            )
            self._trim_locked(chat_id)
            self._connection.commit()

    def touch_chat(self, chat_id: int, username: str | None, *, timestamp: float) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO chats (chat_id, username, first_seen, last_seen, turns)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username  = COALESCE(excluded.username, chats.username),
                    last_seen = excluded.last_seen,
                    turns     = chats.turns + 1
                """,
                (chat_id, username, timestamp, timestamp),
            )
            self._connection.commit()

    def _trim_locked(self, chat_id: int) -> None:
        """Оставляет только последние max_messages_per_chat реплик чата."""
        self._connection.execute(
            """
            DELETE FROM messages
            WHERE chat_id = ?
              AND id NOT IN (
                  SELECT id FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?
              )
            """,
            (chat_id, chat_id, self.max_messages_per_chat),
        )

    # ---------- Чтение ----------

    def load_history(self, chat_id: int, limit: int) -> list[dict[str, str]]:
        """Последние реплики чата в хронологическом порядке.

        Системные записи (например, описания картинок) не возвращаем: бутстрап
        собирается заново при каждом запуске и не должен дублироваться.
        """
        if limit <= 0:
            return []

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM messages
                    WHERE chat_id = ? AND role IN ('user', 'assistant')
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (chat_id, limit),
            ).fetchall()

        history = [{'role': role, 'content': content} for role, content in rows]
        # История должна начинаться с реплики собеседника, иначе модель путается.
        while history and history[0]['role'] != 'user':
            history.pop(0)
        return history

    def clear_chat(self, chat_id: int) -> int:
        with self._lock:
            cursor = self._connection.execute('DELETE FROM messages WHERE chat_id = ?', (chat_id,))
            self._connection.commit()
            return cursor.rowcount

    def stats(self, chat_id: int) -> ChatStats:
        with self._lock:
            row = self._connection.execute(
                'SELECT username, turns FROM chats WHERE chat_id = ?', (chat_id,)
            ).fetchone()
            stored = self._connection.execute(
                'SELECT COUNT(*) FROM messages WHERE chat_id = ?', (chat_id,)
            ).fetchone()[0]

        username, turns = row if row else (None, 0)
        return ChatStats(chat_id=chat_id, username=username, turns=turns, stored_messages=stored)

    def known_chats(self) -> int:
        with self._lock:
            return self._connection.execute('SELECT COUNT(*) FROM chats').fetchone()[0]

    def all_stats(self) -> list[ChatStats]:
        """Все известные чаты с числом обращений. Для панели владельца."""
        with self._lock:
            rows = self._connection.execute(
                'SELECT c.chat_id, c.username, c.turns, '
                '  (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.chat_id) '
                'FROM chats c ORDER BY c.turns DESC'
            ).fetchall()
        return [
            ChatStats(chat_id=chat_id, username=username, turns=turns, stored_messages=stored)
            for chat_id, username, turns, stored in rows
        ]

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except Exception:  # pragma: no cover
                pass
