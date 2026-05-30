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
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_search_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (kind IN ('author', 'institution', 'topic', 'title'))
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
            self._migrate_watch_items(connection)

    def _migrate_watch_items(self, connection: sqlite3.Connection) -> None:
        table_sql_row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'watch_items'
            """
        ).fetchone()
        table_sql = table_sql_row["sql"] if table_sql_row else ""
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(watch_items)").fetchall()
        }
        needs_rebuild = "enabled" not in columns or "last_search_at" not in columns or "'title'" not in table_sql
        if not needs_rebuild:
            return

        has_enabled = "enabled" in columns
        enabled_select = "enabled" if has_enabled else "1"
        has_last_search = "last_search_at" in columns
        last_search_select = "last_search_at" if has_last_search else "''"
        connection.execute("DROP TABLE IF EXISTS watch_items_new")
        connection.execute(
            """
            CREATE TABLE watch_items_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                query TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_search_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (kind IN ('author', 'institution', 'topic', 'title'))
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO watch_items_new (id, kind, label, query, notes, enabled, last_search_at, created_at)
            SELECT id, kind, label, query, notes, {enabled_select}, {last_search_select}, created_at
            FROM watch_items
            """
        )
        connection.execute("DROP TABLE watch_items")
        connection.execute("ALTER TABLE watch_items_new RENAME TO watch_items")

    def list_watch_items(self) -> list[WatchItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, label, query, notes, enabled, last_search_at
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
                enabled=bool(row["enabled"]),
                last_search_at=row["last_search_at"],
            )
            for row in rows
        ]

    def add_watch_item(self, item: WatchItem) -> None:
        if item.kind not in WATCH_TYPES:
            raise ValueError(f"Unsupported watch type: {item.kind}")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO watch_items (kind, label, query, notes, enabled, last_search_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.kind,
                    item.label.strip(),
                    item.query.strip(),
                    item.notes.strip(),
                    1 if item.enabled else 0,
                    item.last_search_at.strip(),
                ),
            )

    def update_watch_item(self, item: WatchItem) -> None:
        if item.id is None:
            raise ValueError("Cannot update a watch item without an id")
        if item.kind not in WATCH_TYPES:
            raise ValueError(f"Unsupported watch type: {item.kind}")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE watch_items
                SET kind = ?, label = ?, query = ?, notes = ?, enabled = ?, last_search_at = ?
                WHERE id = ?
                """,
                (
                    item.kind,
                    item.label.strip(),
                    item.query.strip(),
                    item.notes.strip(),
                    1 if item.enabled else 0,
                    item.last_search_at.strip(),
                    item.id,
                ),
            )

    def set_watch_enabled(self, item_id: int, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watch_items SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, item_id),
            )

    def set_watch_last_search(self, item_id: int, searched_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watch_items SET last_search_at = ? WHERE id = ?",
                (searched_at, item_id),
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
