"""llm/secrets.py: set/status/delete CLI over the OS keyring. All keyring/getpass calls mocked
— the real Windows Credential Manager and the real terminal must never be touched."""
from __future__ import annotations

from unittest.mock import patch

import keyring.errors
import pytest

from llm.secrets import main


def test_set_anthropic_stores_key_from_getpass(capsys):
    with patch("llm.secrets.getpass.getpass", return_value="fake-anthropic-key") as mock_getpass, \
            patch("llm.secrets.keyring.set_password") as mock_set:
        exit_code = main(["set", "anthropic"])

    assert exit_code == 0
    mock_getpass.assert_called_once()
    mock_set.assert_called_once_with("processforge", "llm_api_key_anthropic", "fake-anthropic-key")


def test_set_openrouter_stores_key_from_getpass():
    with patch("llm.secrets.getpass.getpass", return_value="fake-or-key") as mock_getpass, \
            patch("llm.secrets.keyring.set_password") as mock_set:
        exit_code = main(["set", "openrouter"])

    assert exit_code == 0
    mock_getpass.assert_called_once()
    mock_set.assert_called_once_with("processforge", "llm_api_key_openrouter", "fake-or-key")


def test_set_invalid_provider_rejected_cleanly(capsys):
    with patch("llm.secrets.getpass.getpass") as mock_getpass, \
            patch("llm.secrets.keyring.set_password") as mock_set:
        exit_code = main(["set", "ollama"])

    assert exit_code != 0
    mock_getpass.assert_not_called()
    mock_set.assert_not_called()
    captured = capsys.readouterr()
    assert "invalid provider" in captured.err


def test_status_reports_presence_without_leaking_secret(capsys):
    def fake_get_password(service, username):
        if username == "llm_api_key_anthropic":
            return "super-secret-anthropic-value"
        return None

    with patch("llm.secrets.keyring.get_password", side_effect=fake_get_password):
        exit_code = main(["status"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "anthropic: yes" in captured.out
    assert "openrouter: no" in captured.out
    assert "super-secret-anthropic-value" not in captured.out
    assert "super-secret-anthropic-value" not in captured.err


def test_delete_anthropic_calls_keyring_with_correct_args():
    with patch("llm.secrets.keyring.delete_password") as mock_delete:
        exit_code = main(["delete", "anthropic"])

    assert exit_code == 0
    mock_delete.assert_called_once_with("processforge", "llm_api_key_anthropic")


def test_delete_openrouter_calls_keyring_with_correct_args():
    with patch("llm.secrets.keyring.delete_password") as mock_delete:
        exit_code = main(["delete", "openrouter"])

    assert exit_code == 0
    mock_delete.assert_called_once_with("processforge", "llm_api_key_openrouter")


def test_delete_nothing_stored_reports_clean_message_not_traceback(capsys):
    with patch("llm.secrets.keyring.delete_password", side_effect=keyring.errors.PasswordDeleteError):
        exit_code = main(["delete", "anthropic"])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "nothing stored" in captured.err


def test_delete_invalid_provider_rejected_cleanly(capsys):
    with patch("llm.secrets.keyring.delete_password") as mock_delete:
        exit_code = main(["delete", "ollama"])

    assert exit_code != 0
    mock_delete.assert_not_called()
    captured = capsys.readouterr()
    assert "invalid provider" in captured.err


def test_set_missing_provider_arg_raises_systemexit_not_traceback():
    with pytest.raises(SystemExit):
        main(["set"])
