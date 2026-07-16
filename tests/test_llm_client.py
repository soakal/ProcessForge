"""llm/client.py: provider dispatch, env-var checks, request/response shapes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm.client import Tier, complete


def _set_common_env(monkeypatch, provider, model="test-model", api_key="test-key"):
    monkeypatch.setenv("PROCESSFORGE_LLM_PROVIDER", provider)
    monkeypatch.setenv("PROCESSFORGE_MODEL_REASON", model)
    if api_key is not None:
        monkeypatch.setenv("PROCESSFORGE_LLM_API_KEY", api_key)
    else:
        monkeypatch.delenv("PROCESSFORGE_LLM_API_KEY", raising=False)


def _fake_response(json_body):
    response = MagicMock()
    response.json.return_value = json_body
    response.raise_for_status.return_value = None
    return response


def test_anthropic_request_and_response_shape(monkeypatch):
    _set_common_env(monkeypatch, "anthropic", model="claude-x", api_key="secret-anthropic-key")
    messages = [{"role": "user", "content": "hello"}]

    with patch("llm.client.requests.post") as mock_post, \
            patch("llm.client.keyring.get_password") as mock_keyring:
        mock_post.return_value = _fake_response(
            {"content": [{"type": "text", "text": "hi there"}]}
        )
        result = complete(messages, Tier.REASON)

    assert result == "hi there"
    mock_keyring.assert_not_called()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.anthropic.com/v1/messages"
    assert kwargs["headers"]["x-api-key"] == "secret-anthropic-key"
    assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
    assert kwargs["headers"]["content-type"] == "application/json"
    assert kwargs["json"]["model"] == "claude-x"
    assert kwargs["json"]["max_tokens"] == 1024
    assert kwargs["json"]["messages"] == messages
    assert kwargs["timeout"] == 60


def test_openrouter_request_and_response_shape(monkeypatch):
    _set_common_env(monkeypatch, "openrouter", model="or-model", api_key="secret-or-key")
    messages = [{"role": "user", "content": "hello"}]

    with patch("llm.client.requests.post") as mock_post, \
            patch("llm.client.keyring.get_password") as mock_keyring:
        mock_post.return_value = _fake_response(
            {"choices": [{"message": {"content": "openrouter reply"}}]}
        )
        result = complete(messages, Tier.REASON)

    assert result == "openrouter reply"
    mock_keyring.assert_not_called()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://openrouter.ai/api/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-or-key"
    assert kwargs["json"]["model"] == "or-model"
    assert kwargs["json"]["messages"] == messages
    assert kwargs["timeout"] == 60


def test_ollama_request_and_response_shape_default_host(monkeypatch):
    _set_common_env(monkeypatch, "ollama", model="llama3", api_key=None)
    monkeypatch.delenv("PROCESSFORGE_OLLAMA_HOST", raising=False)
    messages = [{"role": "user", "content": "hello"}]

    with patch("llm.client.requests.post") as mock_post, \
            patch("llm.client.keyring.get_password") as mock_keyring:
        mock_post.return_value = _fake_response({"message": {"content": "ollama reply"}})
        result = complete(messages, Tier.REASON)

    assert result == "ollama reply"
    mock_keyring.assert_not_called()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:11434/api/chat"
    assert kwargs["json"]["model"] == "llama3"
    assert kwargs["json"]["messages"] == messages
    assert kwargs["json"]["stream"] is False
    assert kwargs["timeout"] == 60


def test_ollama_request_uses_custom_host(monkeypatch):
    _set_common_env(monkeypatch, "ollama", model="llama3", api_key=None)
    monkeypatch.setenv("PROCESSFORGE_OLLAMA_HOST", "http://example.internal:1234")

    with patch("llm.client.requests.post") as mock_post:
        mock_post.return_value = _fake_response({"message": {"content": "ollama reply"}})
        complete([{"role": "user", "content": "hi"}], Tier.REASON)

    args, _ = mock_post.call_args
    assert args[0] == "http://example.internal:1234/api/chat"


def test_ollama_succeeds_with_no_api_key_in_environment(monkeypatch):
    monkeypatch.setenv("PROCESSFORGE_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PROCESSFORGE_MODEL_REASON", "llama3")
    monkeypatch.delenv("PROCESSFORGE_LLM_API_KEY", raising=False)

    with patch("llm.client.requests.post") as mock_post, \
            patch("llm.client.keyring.get_password") as mock_keyring:
        mock_post.return_value = _fake_response({"message": {"content": "no key needed"}})
        result = complete([{"role": "user", "content": "hi"}], Tier.REASON)

    assert result == "no key needed"
    mock_keyring.assert_not_called()


def test_ollama_never_consults_keyring_when_api_key_env_set(monkeypatch):
    monkeypatch.setenv("PROCESSFORGE_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PROCESSFORGE_MODEL_REASON", "llama3")
    monkeypatch.setenv("PROCESSFORGE_LLM_API_KEY", "unused-key")

    with patch("llm.client.requests.post") as mock_post, \
            patch("llm.client.keyring.get_password") as mock_keyring:
        mock_post.return_value = _fake_response({"message": {"content": "no key needed"}})
        result = complete([{"role": "user", "content": "hi"}], Tier.REASON)

    assert result == "no key needed"
    mock_keyring.assert_not_called()


@pytest.mark.parametrize("provider", [None, "", "bogus-provider"])
def test_missing_or_invalid_provider_raises(monkeypatch, provider):
    if provider is None:
        monkeypatch.delenv("PROCESSFORGE_LLM_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("PROCESSFORGE_LLM_PROVIDER", provider)
    monkeypatch.setenv("PROCESSFORGE_MODEL_REASON", "some-model")
    monkeypatch.setenv("PROCESSFORGE_LLM_API_KEY", "some-key")

    with pytest.raises(RuntimeError, match="anthropic.*openrouter.*ollama"):
        complete([{"role": "user", "content": "hi"}], Tier.REASON)


def test_missing_api_key_raises_for_anthropic(monkeypatch):
    _set_common_env(monkeypatch, "anthropic", model="claude-x", api_key=None)

    with patch("llm.client.keyring.get_password", return_value=None):
        with pytest.raises(RuntimeError, match="PROCESSFORGE_LLM_API_KEY"):
            complete([{"role": "user", "content": "hi"}], Tier.REASON)


def test_missing_api_key_raises_for_openrouter(monkeypatch):
    _set_common_env(monkeypatch, "openrouter", model="or-model", api_key=None)

    with patch("llm.client.keyring.get_password", return_value=None):
        with pytest.raises(RuntimeError, match="PROCESSFORGE_LLM_API_KEY"):
            complete([{"role": "user", "content": "hi"}], Tier.REASON)


def test_keyring_fallback_used_when_env_var_absent_for_anthropic(monkeypatch):
    _set_common_env(monkeypatch, "anthropic", model="claude-x", api_key=None)
    messages = [{"role": "user", "content": "hello"}]

    with patch("llm.client.requests.post") as mock_post, \
            patch("llm.client.keyring.get_password", return_value="keyring-anthropic-key") as mock_keyring:
        mock_post.return_value = _fake_response(
            {"content": [{"type": "text", "text": "hi there"}]}
        )
        result = complete(messages, Tier.REASON)

    assert result == "hi there"
    mock_keyring.assert_called_once_with("processforge", "llm_api_key_anthropic")
    args, kwargs = mock_post.call_args
    assert kwargs["headers"]["x-api-key"] == "keyring-anthropic-key"


def test_keyring_fallback_used_when_env_var_blank_for_openrouter(monkeypatch):
    _set_common_env(monkeypatch, "openrouter", model="or-model", api_key=None)
    monkeypatch.setenv("PROCESSFORGE_LLM_API_KEY", "")
    messages = [{"role": "user", "content": "hello"}]

    with patch("llm.client.requests.post") as mock_post, \
            patch("llm.client.keyring.get_password", return_value="keyring-or-key") as mock_keyring:
        mock_post.return_value = _fake_response(
            {"choices": [{"message": {"content": "openrouter reply"}}]}
        )
        result = complete(messages, Tier.REASON)

    assert result == "openrouter reply"
    mock_keyring.assert_called_once_with("processforge", "llm_api_key_openrouter")
    args, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer keyring-or-key"


def test_both_absent_raises_for_anthropic(monkeypatch):
    _set_common_env(monkeypatch, "anthropic", model="claude-x", api_key=None)

    with patch("llm.client.keyring.get_password", return_value=None) as mock_keyring:
        with pytest.raises(RuntimeError, match="PROCESSFORGE_LLM_API_KEY"):
            complete([{"role": "user", "content": "hi"}], Tier.REASON)

    mock_keyring.assert_called_once_with("processforge", "llm_api_key_anthropic")


def test_both_absent_raises_for_openrouter(monkeypatch):
    _set_common_env(monkeypatch, "openrouter", model="or-model", api_key=None)

    with patch("llm.client.keyring.get_password", return_value=None) as mock_keyring:
        with pytest.raises(RuntimeError, match="PROCESSFORGE_LLM_API_KEY"):
            complete([{"role": "user", "content": "hi"}], Tier.REASON)

    mock_keyring.assert_called_once_with("processforge", "llm_api_key_openrouter")


@pytest.mark.parametrize("provider", ["anthropic", "openrouter", "ollama"])
def test_missing_model_env_raises_for_all_providers(monkeypatch, provider):
    monkeypatch.setenv("PROCESSFORGE_LLM_PROVIDER", provider)
    monkeypatch.delenv("PROCESSFORGE_MODEL_REASON", raising=False)
    monkeypatch.setenv("PROCESSFORGE_LLM_API_KEY", "some-key")

    with pytest.raises(RuntimeError, match="PROCESSFORGE_MODEL_REASON"):
        complete([{"role": "user", "content": "hi"}], Tier.REASON)
