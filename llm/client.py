"""Single LLM access point. Stages call ctx.complete(messages, tier) and never name a model."""
from __future__ import annotations
import os
from enum import Enum


class Tier(str, Enum):
    EXTRACT = "extract"    # cheap/local
    REASON = "reason"      # Sonnet-class
    ARBITER = "arbiter"    # Opus


_TIER_MODEL_ENV = {
    Tier.EXTRACT: "PROCESSFORGE_MODEL_EXTRACT",
    Tier.REASON: "PROCESSFORGE_MODEL_REASON",
    Tier.ARBITER: "PROCESSFORGE_MODEL_ARBITER",
}


def complete(messages: list[dict], tier: Tier) -> str:
    """Route messages to the model bound to `tier`. Wire to the OpenRouter/Hermes router here.

    messages: standard [{"role": ..., "content": ...}, ...]
    Never log `messages` or the resolved API key.
    """
    model_env = _TIER_MODEL_ENV[tier]
    model = os.environ.get(model_env)
    api_key = os.environ.get("PROCESSFORGE_LLM_API_KEY")
    if not api_key:
        raise RuntimeError(f"PROCESSFORGE_LLM_API_KEY is not set (needed for tier={tier.value})")
    if not model:
        raise RuntimeError(f"{model_env} is not set (needed for tier={tier.value})")
    raise NotImplementedError("wire complete() to the OpenRouter/Hermes router")
