"""auth/users.py: create/list/delete CLI over AuthRepository. getpass.getpass is mocked
— the real terminal must never be touched — and the real production migration path
(pipeline._migrate) backs a per-test tmp_path sqlite db."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import pipeline
from auth.repository import AuthRepository
from auth.users import main


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "pf.db")
    pipeline._migrate(path)
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", path)
    return path


def test_create_stores_operator_from_getpass(capsys, db_path):
    with patch("auth.users.getpass.getpass", return_value="a-valid-password") as mock_getpass:
        exit_code = main(["create", "alice"])

    assert exit_code == 0
    mock_getpass.assert_called_once()
    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("alice") is not None
    finally:
        repo.close()
    captured = capsys.readouterr()
    assert "alice" in captured.out


def test_create_empty_password_rejected_cleanly(capsys, db_path):
    with patch("auth.users.getpass.getpass", return_value="") as mock_getpass:
        exit_code = main(["create", "bob"])

    assert exit_code != 0
    mock_getpass.assert_called_once()
    captured = capsys.readouterr()
    assert "empty" in captured.err.lower()

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("bob") is None
    finally:
        repo.close()


def test_create_whitespace_only_password_rejected_cleanly(capsys, db_path):
    with patch("auth.users.getpass.getpass", return_value="   ") as mock_getpass:
        exit_code = main(["create", "carol"])

    assert exit_code != 0
    mock_getpass.assert_called_once()
    captured = capsys.readouterr()
    assert "empty" in captured.err.lower()

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("carol") is None
    finally:
        repo.close()


def test_create_too_short_password_rejected_cleanly(capsys, db_path):
    with patch("auth.users.getpass.getpass", return_value="short") as mock_getpass:
        exit_code = main(["create", "dave"])

    assert exit_code != 0
    mock_getpass.assert_called_once()
    captured = capsys.readouterr()
    assert "8" in captured.err

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("dave") is None
    finally:
        repo.close()


def test_create_duplicate_username_rejected_cleanly_not_traceback(capsys, db_path):
    with patch("auth.users.getpass.getpass", return_value="first-password"):
        exit_code = main(["create", "erin"])
    assert exit_code == 0

    with patch("auth.users.getpass.getpass", return_value="second-password") as mock_getpass:
        exit_code = main(["create", "erin"])

    assert exit_code != 0
    mock_getpass.assert_called_once()
    captured = capsys.readouterr()
    assert "already exists" in captured.err


def test_list_shows_created_usernames(capsys, db_path):
    with patch("auth.users.getpass.getpass", return_value="frank-password"):
        main(["create", "frank"])
    with patch("auth.users.getpass.getpass", return_value="grace-password"):
        main(["create", "grace"])
    capsys.readouterr()

    exit_code = main(["list"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "frank" in captured.out
    assert "grace" in captured.out


def test_delete_removes_operator(capsys, db_path):
    with patch("auth.users.getpass.getpass", return_value="henry-password"):
        main(["create", "henry"])
    capsys.readouterr()

    exit_code = main(["delete", "henry"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "henry" in captured.out

    repo = AuthRepository(db_path)
    try:
        assert repo.get_operator("henry") is None
    finally:
        repo.close()


def test_delete_nonexistent_username_rejected_cleanly_not_traceback(capsys, db_path):
    exit_code = main(["delete", "nobody"])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()


def test_create_missing_username_arg_raises_systemexit_not_traceback(db_path):
    with pytest.raises(SystemExit):
        main(["create"])


def test_full_sequence_never_leaks_password_in_stdout_or_stderr(capsys, db_path):
    secret_password = "super-secret-operator-password"
    with patch("auth.users.getpass.getpass", return_value=secret_password):
        main(["create", "ivy"])
    main(["list"])
    main(["delete", "ivy"])

    captured = capsys.readouterr()
    assert secret_password not in captured.out
    assert secret_password not in captured.err
