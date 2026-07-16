"""Shared test fixtures for the whole tests/ tree (including tests/seams/).

Zero-network-calls-in-tests guard: importing api.main calls load_dotenv() at
module level, which — on a machine with a real PROCESSFORGE_LLM_PROVIDER set
in .env/keyring — would otherwise leak into every test running in the same
pytest process and cause llm.client.complete() to attempt a real, billable
API call. This autouse fixture strips PROCESSFORGE_LLM_PROVIDER from the
environment before every test, so llm.client.complete() always raises its
own RuntimeError ("provider not set") instead of reaching the network,
unless a test explicitly sets its own provider via monkeypatch.setenv within
its own body (which layers on top of this fixture and is unaffected).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_llm_provider(monkeypatch):
    monkeypatch.delenv("PROCESSFORGE_LLM_PROVIDER", raising=False)
