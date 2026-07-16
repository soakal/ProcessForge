"""Tests for auth.repository.AuthRepository: operator + token CRUD backing
real auth, built against the real production migration path (pipeline._migrate)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import pipeline
from auth.repository import AuthRepository, DuplicateOperatorError


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "pf.db")
    pipeline._migrate(db_path)
    r = AuthRepository(db_path)
    yield r
    r.close()


def test_create_and_get_operator(repo):
    repo.create_operator("alice", "correct horse battery staple")

    got = repo.get_operator("alice")

    assert got is not None
    assert got["username"] == "alice"
    assert got["password_hash"] != "correct horse battery staple"
    assert got["password_hash"].startswith("pbkdf2_sha256$")


def test_get_operator_nonexistent_returns_none(repo):
    assert repo.get_operator("nobody") is None


def test_create_operator_duplicate_username_raises_duplicate_error(repo):
    repo.create_operator("bob", "first-password")

    with pytest.raises(DuplicateOperatorError):
        repo.create_operator("bob", "second-password")


def test_create_token_and_get_operator_by_token_resolves_correct_operator(repo):
    operator_id = repo.create_operator("carol", "hunter2hunter2")
    token = repo.create_token(operator_id)

    resolved = repo.get_operator_by_token(token)

    assert resolved is not None
    assert resolved["id"] == operator_id
    assert resolved["username"] == "carol"


def test_get_operator_by_token_bogus_token_returns_none(repo):
    assert repo.get_operator_by_token("this-token-does-not-exist") is None


def test_get_operator_by_token_expired_returns_none_same_as_nonexistent(repo, tmp_path):
    operator_id = repo.create_operator("dave", "expiring-password")
    token = repo.create_token(operator_id)

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(str(tmp_path / "pf.db"))
    try:
        conn.execute("UPDATE auth_tokens SET expires_at = ? WHERE token = ?", (past, token))
        conn.commit()
    finally:
        conn.close()

    expired_result = repo.get_operator_by_token(token)
    nonexistent_result = repo.get_operator_by_token("also-does-not-exist")

    assert expired_result is None
    assert nonexistent_result is None
    assert expired_result == nonexistent_result


def test_delete_token_invalidates_it_and_is_idempotent(repo):
    operator_id = repo.create_operator("erin", "delete-me-password")
    token = repo.create_token(operator_id)

    repo.delete_token(token)

    assert repo.get_operator_by_token(token) is None

    # Deleting an already-deleted (or never-existent) token must not raise.
    repo.delete_token(token)
    repo.delete_token("never-existed-either")


def test_list_operators_excludes_password_hash(repo):
    repo.create_operator("frank", "frank-password")
    repo.create_operator("grace", "grace-password")

    operators = repo.list_operators()
    usernames = {op["username"] for op in operators}

    assert {"frank", "grace"} <= usernames
    for op in operators:
        assert "password_hash" not in op
        assert "created_at" in op
