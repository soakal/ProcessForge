"""Auth repository: operator + token CRUD backing real auth.

Mirrors KBRepository's sqlite3 connect / PRAGMA foreign_keys=ON / row_factory
shape (kb/repository.py). Assumes the schema is already migrated (see
kb/migrations/versions/3a99fe629a01_auth_tables.py) — this module never
creates or alters tables.
"""
from __future__ import annotations

import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from auth.hashing import hash_password

_TOKEN_TTL = timedelta(days=7)


class DuplicateOperatorError(ValueError):
    """Raised when create_operator() is called with a username that already exists."""


class OperatorNotFoundError(ValueError):
    """Raised when delete_operator() is called with a username that doesn't exist."""


class AuthRepository:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def create_operator(self, username: str, password: str) -> str:
        operator_id = str(uuid.uuid4())
        password_hash = hash_password(password)
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                "INSERT INTO operators (id, username, password_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (operator_id, username, password_hash, created_at),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateOperatorError(f"operator username already exists: {username!r}") from exc
        return operator_id

    def get_operator(self, username: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, username, password_hash, created_at FROM operators WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None

    def create_token(self, operator_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        created_at = now.isoformat()
        expires_at = (now + _TOKEN_TTL).isoformat()
        self._conn.execute(
            "INSERT INTO auth_tokens (token, operator_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token, operator_id, created_at, expires_at),
        )
        self._conn.commit()
        return token

    def get_operator_by_token(self, token: str) -> dict | None:
        token_row = self._conn.execute(
            "SELECT operator_id, expires_at FROM auth_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if token_row is None:
            return None

        expires_at = datetime.fromisoformat(token_row["expires_at"])
        if not expires_at > datetime.now(timezone.utc):
            return None

        operator_row = self._conn.execute(
            "SELECT id, username, created_at FROM operators WHERE id = ?",
            (token_row["operator_id"],),
        ).fetchone()
        return dict(operator_row) if operator_row else None

    def delete_token(self, token: str) -> None:
        self._conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
        self._conn.commit()

    def delete_operator(self, username: str) -> None:
        operator = self.get_operator(username)
        if operator is None:
            raise OperatorNotFoundError(f"operator username not found: {username!r}")
        # No ON DELETE CASCADE on auth_tokens.operator_id (see migration
        # 3a99fe629a01_auth_tables.py) — delete the operator's tokens first
        # so no auth_tokens row is left referencing a deleted operator_id.
        self._conn.execute("DELETE FROM auth_tokens WHERE operator_id = ?", (operator["id"],))
        self._conn.execute("DELETE FROM operators WHERE username = ?", (username,))
        self._conn.commit()

    def list_operators(self) -> list[dict]:
        rows = self._conn.execute("SELECT username, created_at FROM operators").fetchall()
        return [dict(row) for row in rows]
