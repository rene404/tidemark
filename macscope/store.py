"""SQLite event journal. All writes use parameterized SQL and explicit transactions."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError("Das Datenverzeichnis muss dir gehören und darf kein Symlink sein.")
    path.chmod(0o700)


def load_token(directory: Path) -> str:
    path = directory / "access-token"
    # O_NOFOLLOW prevents an accidental overwrite/read through a symbolic link.
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise ValueError("Die Schlüsseldatei muss dir gehören.")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "r+", encoding="ascii", closefd=False) as stream:
            token = stream.read().strip()
            if not token:
                token = secrets.token_urlsafe(32)
                stream.seek(0)
                stream.write(token + "\n")
                stream.truncate()
                stream.flush()
                os.fsync(fd)
            if len(token) < 32:
                raise ValueError("Ungültige Schlüsseldatei.")
            return token
    finally:
        os.close(fd)


class Store:
    def __init__(self, directory: Path, retention_days: int = 30):
        private_directory(directory)
        self.path = directory / "timeline.sqlite3"
        self.retention_days = retention_days
        if self.path.is_symlink():
            raise ValueError("Die Datenbank darf kein Symlink sein.")
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    kind TEXT NOT NULL,
                    app_name TEXT,
                    bundle_id TEXT,
                    pid INTEGER,
                    details TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS events_time ON events(timestamp);
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    target TEXT NOT NULL,
                    message TEXT NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _event(self, connection, kind, app=None, details=None, timestamp=None):
        app = app or {}
        cursor = connection.execute(
            "INSERT INTO events(timestamp,kind,app_name,bundle_id,pid,details) VALUES(?,?,?,?,?,?)",
            (time.time() if timestamp is None else timestamp, kind,
             app.get("name"), app.get("bundle_id"), app.get("pid"),
             json.dumps(details or {}, ensure_ascii=False)),
        )
        return cursor.lastrowid

    def event(self, kind: str, app=None, details=None, timestamp=None) -> int:
        with self.connect() as connection:
            return self._event(connection, kind, app, details, timestamp)

    def events(self, *, before=None, after=None, limit=100, query="", kind=""):
        if before is not None and after is not None:
            raise ValueError("before und after dürfen nicht kombiniert werden.")
        conditions, values = [], []
        if before is not None:
            conditions.append("id < ?")
            values.append(before)
        if after is not None:
            conditions.append("id > ?")
            values.append(after)
        if query:
            conditions.append("(app_name LIKE ? ESCAPE '\\' OR bundle_id LIKE ? ESCAPE '\\')")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.extend([f"%{escaped}%"] * 2)
        if kind:
            conditions.append("kind = ?")
            values.append(kind)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        order = "ASC" if after is not None else "DESC"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM events{where} ORDER BY id {order} LIMIT ?",
                [*values, limit + 1],
            ).fetchall()
        result = []
        for row in rows[:limit]:
            item = dict(row)
            item["details"] = json.loads(item["details"])
            result.append(item)
        return {"events": result, "has_more": len(rows) > limit}

    def create_command(self, target: dict[str, Any]) -> str:
        command_id = secrets.token_hex(16)
        now = time.time()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO commands VALUES(?,?,?,?,?,?)",
                (command_id, now, now, "queued", json.dumps(target), "Wartet auf den macOS-Agenten."),
            )
            self._event(connection, "action.queued", target, {"command_id": command_id, "action": "quit"})
        return command_id

    def set_command(self, command_id: str, status: str, message: str):
        with self.connect() as connection:
            row = connection.execute("SELECT target FROM commands WHERE id=?", (command_id,)).fetchone()
            if row is None:
                raise ValueError("Unbekannter Auftrag.")
            connection.execute(
                "UPDATE commands SET status=?,message=?,updated_at=? WHERE id=?",
                (status, message, time.time(), command_id),
            )
            self._event(connection, "action." + status, json.loads(row[0]),
                        {"command_id": command_id, "message": message})

    def command(self, command_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["target"] = json.loads(result["target"])
        return result

    def recover_commands(self, message="Agent neu gestartet; Auftrag wird nicht wiederholt."):
        # Never replay a remote action after a restart: the original PID may have been reused.
        with self.connect() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM commands WHERE status IN ('queued','requested')"
            )]
        for command_id in ids:
            self.set_command(command_id, "unconfirmed", message)

    def prune(self):
        cutoff = time.time() - self.retention_days * 86400
        with self.connect() as connection:
            connection.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            connection.execute("DELETE FROM commands WHERE updated_at < ?", (cutoff,))
