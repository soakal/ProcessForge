"""desktop/setup_account.py: create_account() GUI-free account creation,
backing the tkinter operator setup wizard. Tests drive create_account()
directly — no tkinter root is ever constructed, and the real production
migration path (pipeline._migrate) backs a per-test tmp_path sqlite db, same
pattern as tests/test_auth_users_cli.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import pipeline
from auth.repository import AuthRepository, DuplicateOperatorError, OperatorNotFoundError
from desktop.setup_account import (
    AccountValidationError,
    _project_root,
    create_account,
    update_password,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pf.db")
    pipeline._migrate(path)
    return path


def test_create_account_success_stores_operator(db_path):
    operator_id = create_account("alice", "a-valid-password", "a-valid-password", db_path)

    assert isinstance(operator_id, str) and operator_id
    repo = AuthRepository(db_path)
    try:
        stored = repo.get_operator("alice")
    finally:
        repo.close()
    assert stored is not None
    assert stored["username"] == "alice"
    assert stored["id"] == operator_id


def test_create_account_duplicate_username_raises(db_path):
    create_account("bob", "first-password", "first-password", db_path)

    with pytest.raises(DuplicateOperatorError):
        create_account("bob", "second-password", "second-password", db_path)


def test_create_account_empty_username_rejected(db_path):
    with pytest.raises(AccountValidationError):
        create_account("", "a-valid-password", "a-valid-password", db_path)


def test_create_account_whitespace_only_username_rejected(db_path):
    with pytest.raises(AccountValidationError):
        create_account("   ", "a-valid-password", "a-valid-password", db_path)


def test_create_account_empty_password_rejected(db_path):
    with pytest.raises(AccountValidationError):
        create_account("carol", "", "", db_path)


def test_create_account_short_password_rejected(db_path):
    with pytest.raises(AccountValidationError):
        create_account("frank", "short", "short", db_path)

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("frank") is None
    finally:
        repo.close()


def test_create_account_mismatched_confirm_rejected(db_path):
    with pytest.raises(AccountValidationError):
        create_account("dave", "a-valid-password", "different-password", db_path)


def test_create_account_validation_error_never_touches_db(db_path):
    with pytest.raises(AccountValidationError):
        create_account("erin", "a-valid-password", "mismatched", db_path)

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("erin") is None
    finally:
        repo.close()


def test_create_account_missing_username_arg_still_type_ok(db_path):
    # Guards against a str-coercion regression: whitespace-only username must
    # be rejected the same way as a truly empty string.
    with pytest.raises(AccountValidationError):
        create_account("\t\n ", "a-valid-password", "a-valid-password", db_path)


def test_update_password_changes_hash_for_existing_operator(db_path):
    create_account("grace", "original-password", "original-password", db_path)
    repo = AuthRepository(db_path)
    try:
        old_hash = repo.get_operator("grace")["password_hash"]
    finally:
        repo.close()

    update_password("grace", "replacement-password", "replacement-password", db_path)

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("grace")["password_hash"] != old_hash
    finally:
        repo.close()


def test_update_password_strips_username(db_path):
    create_account("heidi", "original-password", "original-password", db_path)

    # A trailing space must resolve to the same stored (stripped) operator.
    update_password("heidi ", "replacement-password", "replacement-password", db_path)

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("heidi") is not None
    finally:
        repo.close()


def test_update_password_unknown_username_raises(db_path):
    with pytest.raises(OperatorNotFoundError):
        update_password("nobody", "a-valid-password", "a-valid-password", db_path)


def test_update_password_mismatched_confirm_rejected(db_path):
    create_account("ivan", "original-password", "original-password", db_path)
    with pytest.raises(AccountValidationError):
        update_password("ivan", "a-valid-password", "different-password", db_path)


def test_project_root_not_frozen_uses_module_grandparent(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert _project_root() == Path(__file__).resolve().parent.parent


def test_project_root_frozen_uses_executable_parent(monkeypatch, tmp_path):
    fake_exe = tmp_path / "ProcessForgeSetup.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert _project_root() == fake_exe.resolve().parent
