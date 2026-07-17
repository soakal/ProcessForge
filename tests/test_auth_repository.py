"""Tests for auth.repository.AuthRepository: operator + token CRUD backing
real auth, built against the real production migration path (pipeline._migrate)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import pipeline
from auth.repository import AuthRepository, DuplicateOperatorError, OperatorNotFoundError


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


def test_delete_operator_removes_it(repo):
    repo.create_operator("henry", "henry-password")

    repo.delete_operator("henry")

    assert repo.get_operator("henry") is None


def test_delete_operator_nonexistent_raises_operator_not_found_error(repo):
    with pytest.raises(OperatorNotFoundError):
        repo.delete_operator("nobody")


def test_delete_operator_also_removes_its_tokens(repo, tmp_path):
    operator_id = repo.create_operator("ivy", "ivy-password")
    token = repo.create_token(operator_id)

    assert repo.get_operator_by_token(token) is not None

    repo.delete_operator("ivy")

    assert repo.get_operator_by_token(token) is None

    conn = sqlite3.connect(str(tmp_path / "pf.db"))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM auth_tokens WHERE operator_id = ?", (operator_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 0


def test_set_password_changes_hash_and_keeps_operator(repo):
    repo.create_operator("penny", "old-password")
    old_hash = repo.get_operator("penny")["password_hash"]

    repo.set_password("penny", "brand-new-password")

    new_hash = repo.get_operator("penny")["password_hash"]
    assert new_hash != old_hash
    assert new_hash.startswith("pbkdf2_sha256$")


def test_set_password_nonexistent_raises_operator_not_found_error(repo):
    with pytest.raises(OperatorNotFoundError):
        repo.set_password("nobody", "some-password")


def test_set_password_revokes_existing_tokens(repo):
    operator_id = repo.create_operator("quinn", "first-password")
    token = repo.create_token(operator_id)
    assert repo.get_operator_by_token(token) is not None

    repo.set_password("quinn", "second-password")

    # A password change must invalidate old sessions.
    assert repo.get_operator_by_token(token) is None


def test_list_operators_excludes_password_hash(repo):
    repo.create_operator("frank", "frank-password")
    repo.create_operator("grace", "grace-password")

    operators = repo.list_operators()
    usernames = {op["username"] for op in operators}

    assert {"frank", "grace"} <= usernames
    for op in operators:
        assert "password_hash" not in op
        assert "created_at" in op
