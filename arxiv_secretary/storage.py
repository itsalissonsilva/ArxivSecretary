from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import WATCH_TYPES, WatchItem


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    query TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (kind IN ('author', 'institution', 'topic'))
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def list_watch_items(self) -> list[WatchItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, label, query, notes
                FROM watch_items
                ORDER BY kind, label COLLATE NOCASE
                """
            ).fetchall()
        return [
            WatchItem(
                id=row["id"],
                kind=row["kind"],
                label=row["label"],
                query=row["query"],
                notes=row["notes"],
            )
            for row in rows
        ]

    def add_watch_item(self, item: WatchItem) -> None:
        if item.kind not in WATCH_TYPES:
            raise ValueError(f"Unsupported watch type: {item.kind}")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO watch_items (kind, label, query, notes)
                VALUES (?, ?, ?, ?)
                """,
                (item.kind, item.label.strip(), item.query.strip(), item.notes.strip()),
            )

    def update_watch_item(self, item: WatchItem) -> None:
        if item.id is None:
            raise ValueError("Cannot update a watch item without an id")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE watch_items
                SET kind = ?, label = ?, query = ?, notes = ?
                WHERE id = ?
                """,
                (item.kind, item.label.strip(), item.query.strip(), item.notes.strip(), item.id),
            )

    def delete_watch_item(self, item_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM watch_items WHERE id = ?", (item_id,))

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
