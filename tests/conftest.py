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


@pytest.fixture(scope="session", autouse=True)
def _load_api_main_once():
    """Force api.main's module-level load_dotenv() to run once, up front.

    Session-scoped autouse fixtures are set up before function-scoped ones,
    so this guarantees load_dotenv() runs against the untouched real
    environment before the first test's _no_real_llm_provider delenv fires.
    Without this, whichever test is first in the session to trigger
    `from api.main import app` (e.g. via TestClient construction) would
    import api.main *after* its own delenv, letting dotenv (override=False)
    re-populate PROCESSFORGE_LLM_PROVIDER from a real .env for that one
    test and causing a real network call.
    """
    import api.main  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    from api.main import _rate_limit_buckets
    _rate_limit_buckets.clear()
    yield


@pytest.fixture(autouse=True)
def _no_real_llm_provider(monkeypatch):
    monkeypatch.delenv("PROCESSFORGE_LLM_PROVIDER", raising=False)
