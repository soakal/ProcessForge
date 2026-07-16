"""Single LLM access point. Stages call ctx.complete(messages, tier) and never name a model."""
from __future__ import annotations
import os
from enum import Enum

import requests

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"
_TIMEOUT_S = 60


class Tier(str, Enum):
    EXTRACT = "extract"    # cheap/local
    REASON = "reason"      # Sonnet-class
    ARBITER = "arbiter"    # Opus


_TIER_MODEL_ENV = {
    Tier.EXTRACT: "PROCESSFORGE_MODEL_EXTRACT",
    Tier.REASON: "PROCESSFORGE_MODEL_REASON",
    Tier.ARBITER: "PROCESSFORGE_MODEL_ARBITER",
}

_VALID_PROVIDERS = ("anthropic", "openrouter", "ollama")


def _complete_anthropic(messages: list[dict], model: str, api_key: str) -> str:
    response = requests.post(
        _ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 1024, "messages": messages},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"]


def _complete_openrouter(messages: list[dict], model: str, api_key: str) -> str:
    response = requests.post(
        _OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": messages},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _complete_ollama(messages: list[dict], model: str) -> str:
    host = os.environ.get("PROCESSFORGE_OLLAMA_HOST") or _DEFAULT_OLLAMA_HOST
    response = requests.post(
        f"{host}/api/chat",
        json={"model": model, "messages": messages, "stream": False},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def complete(messages: list[dict], tier: Tier) -> str:
    """Route messages to the model bound to `tier`, via the provider named by
    PROCESSFORGE_LLM_PROVIDER (anthropic | openrouter | ollama).

    messages: standard [{"role": ..., "content": ...}, ...]
    Never log `messages` or the resolved API key.
    """
    provider = os.environ.get("PROCESSFORGE_LLM_PROVIDER")
    if provider not in _VALID_PROVIDERS:
        raise RuntimeError(
            f"PROCESSFORGE_LLM_PROVIDER is not set to a supported provider "
            f"(valid options: {', '.join(_VALID_PROVIDERS)})"
        )

    model_env = _TIER_MODEL_ENV[tier]
    model = os.environ.get(model_env)
    if not model:
        raise RuntimeError(f"{model_env} is not set (needed for tier={tier.value})")

    if provider == "ollama":
        return _complete_ollama(messages, model)

    api_key = os.environ.get("PROCESSFORGE_LLM_API_KEY")
    if not api_key:
        raise RuntimeError(f"PROCESSFORGE_LLM_API_KEY is not set (needed for tier={tier.value})")

    if provider == "anthropic":
        return _complete_anthropic(messages, model, api_key)
    return _complete_openrouter(messages, model, api_key)
