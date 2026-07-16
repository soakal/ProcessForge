"""Migration test for 3a99fe629a01_auth_tables: confirms the `operators` and
`auth_tokens` tables land correctly via the real production migration path
(pipeline._migrate), including the UNIQUE(username) constraint and the
auth_tokens -> operators foreign key declaration."""
from __future__ import annotations

import sqlite3

import pytest

import pipeline


def _migrated_db(tmp_path):
    db_path = str(tmp_path / "auth_migration_test.db")
    pipeline._migrate(db_path)
    return db_path


def test_operators_table_has_expected_columns(tmp_path):
    db_path = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(operators)")}
    finally:
        conn.close()

    assert set(cols) == {"id", "username", "password_hash", "created_at"}


def test_auth_tokens_table_has_expected_columns(tmp_path):
    db_path = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(auth_tokens)")}
    finally:
        conn.close()

    assert set(cols) == {"token", "operator_id", "created_at", "expires_at"}


def test_operators_username_unique_constraint_enforced(tmp_path):
    db_path = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO operators (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            ("op-1", "alice", "hash-1", "2026-07-16T00:00:00Z"),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO operators (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                ("op-2", "alice", "hash-2", "2026-07-16T00:00:01Z"),
            )
    finally:
        conn.close()


def test_auth_tokens_operator_id_foreign_key_declared(tmp_path):
    db_path = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        fks = conn.execute("PRAGMA foreign_key_list(auth_tokens)").fetchall()
    finally:
        conn.close()

    assert len(fks) == 1
    fk = fks[0]
    # PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, match
    assert fk[2] == "operators"
    assert fk[3] == "operator_id"
    assert fk[4] == "id"


def test_auth_tokens_operator_id_index_exists(tmp_path):
    db_path = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'ix_auth_tokens_operator_id'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
