"""Public lead intake feature (docs/FEATURE-SPEC-public-lead-intake.md), Item
4 — hardening proof / adversarial suite. No production code changes are
expected here; this file's job is to PROVE, not merely assert, the
guarantees Items 1-2 already claim:

  (a) Zero-LLM-egress proof (the load-bearing test): drives the full public
      flow (start -> 6 answers -> completion) with a real-looking provider
      configured (PROCESSFORGE_LLM_PROVIDER=openrouter, a dummy API key, and
      a placeholder PROCESSFORGE_MODEL_EXTRACT so the transport call site is
      actually reachable, set from inside the test body, i.e. AFTER
      tests/conftest.py's autouse fixture has already stripped/reset the env
      for this test) and `requests.post` replaced with a call-recording spy
      (not a raising rig — stages/interviewer.py's `except Exception:`
      fallbacks would swallow a raised exception and let the test pass
      regardless of egress), asserted never invoked after the flow
      completes. This is the same proof-by-instrumented-transport technique
      CLAUDE.md documents for tests/conftest.py's own guarantee (verified
      "closed by proof, not inference"), applied specifically to the public
      path so a future regression (someone swapping
      `_next_question_deterministic` for `next_question`, or dropping
      `_NoLLMCtx` in favor of a real `_Ctx`) fails this test rather than
      silently reaching a real, billable API call once deployed with a real
      provider configured.
  (b) Auth boundary: the authenticated review-path endpoints Brian's UI
      relies on (`GET /businesses`, `GET /businesses/{id}/sessions`,
      `GET /interviews/{sid}/transcript`) still 401 with no/garbage auth
      when queried with `tenant=public-leads` — the public tenant name is
      not a backdoor into the authenticated surface.
  (c) Capability containment: a public session_id used against the
      *operator* `POST /interviews/{sid}/answer` route without a token
      still 401s (auth fires before any session lookup); with a valid
      operator token and `tenant=public-leads` it works as designed (D9's
      intended "Brian takes over a lead" path, not a hole); against any
      other tenant it 404s identically to an unknown id (G2's structural
      tenant isolation).
  (d) Flood/honeypot at volume: driving the public start endpoint past its
      rate limit stops row growth at exactly the allowed count, and
      submitting the honeypot at volume never creates the database file at
      all (checked before any repo/db access, per D6).
"""
from __future__ import annotations

import os
import sqlite3
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from auth.repository import AuthRepository
from kb.repository import KBRepository
from pipeline import _migrate

_PUBLIC_TENANT = "public-leads"


def _client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _set_env(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", db_path)
    return db_path


@pytest.fixture(autouse=True)
def _reset_public_and_operator_rate_limit_buckets():
    """tests/conftest.py's autouse fixture only clears the operator bucket
    dict (_rate_limit_buckets); this file drives the public rate limiter
    directly (group d) and indirectly (every helper below calls
    /public/intake), so its own bucket dict needs the same per-test reset
    (mirrors tests/test_public_intake_api.py's identical fixture)."""
    from api.main import _public_rate_limit_buckets, _rate_limit_buckets

    _public_rate_limit_buckets.clear()
    _rate_limit_buckets.clear()
    yield


def _seed_operator(db_path, username="alice", password="correct-horse-battery"):  # nosec B107 - test fixture only, not a real credential
    """Migrate the schema and create an operator directly via AuthRepository
    (mirrors tests/test_public_intake_api.py's own helper of the same
    name) — needed for group (c)'s legitimate-operator-takeover case."""
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        repo.create_operator(username, password)
    finally:
        repo.close()


def _login_token(client, db_path, username="alice", password="correct-horse-battery"):  # nosec B107 - test fixture only, not a real credential
    _seed_operator(db_path, username=username, password=password)
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["token"]


def _start_public(client, business_name="Acme Leads Co", contact="lead@example.com"):
    response = client.post(
        "/public/intake", json={"business_name": business_name, "contact": contact}
    )
    assert response.status_code == 200
    return response.json()


def _answer_public(client, session_id, answer):
    return client.post(f"/public/intake/{session_id}/answer", json={"answer": answer})


def _drive_public_to_completion(client, session_id):
    """Submits the 6 answers that follow the opener; response 6 is always
    the completion body (mirrors tests/test_public_intake_api.py's helper
    of the same name)."""
    contents = [
        "answering the opener",
        "about 2 hours, once a week",
        "a clean automated report",
        "a shared drive folder",
        "only rows marked open",
        "an Excel file",
    ]
    return [_answer_public(client, session_id, content) for content in contents]


# --- (a) Zero-LLM-egress proof ---


def test_public_intake_full_flow_makes_zero_llm_egress_calls(monkeypatch, tmp_path):
    """The load-bearing proof (Item 4a). Configure a real-looking provider +
    key + tier model from inside this test's own body — layering on top of,
    and running strictly after, tests/conftest.py's autouse delenv fixture
    has already fired for this test — so the transport call site
    (`requests.post`, which llm/client.py's `_complete_openrouter` calls) is
    actually reachable, then replace it with a call-recording spy and assert
    it was never invoked. Drive the complete public flow end to end: every
    request must still succeed, the deterministic pipeline artifacts must
    exist, AND the spy's call count must be zero, proving the public path
    never reaches the network even when a real provider is configured,
    exactly as it would be on the deployed box."""
    db_path = _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PROCESSFORGE_LLM_API_KEY", "dummy-key-not-a-real-secret")
    # Also configure the tier model llm/client.py's complete() requires
    # (Tier.EXTRACT, the only tier the public path's interviewer.run() ever
    # requests) so the transport call site is actually reachable in this
    # test, matching how a real deployed box would be configured — without
    # this, complete() raises before ever reaching requests.post regardless
    # of whether the _NoLLMCtx guard is in place, making the rig below moot.
    monkeypatch.setenv("PROCESSFORGE_MODEL_EXTRACT", "placeholder-extract-model")

    # A call-recording spy rather than a raising rig: stages/interviewer.py's
    # `run`/`next_question` both wrap the LLM call in `except Exception:` and
    # silently fall back to the deterministic path, which would swallow a
    # raised AssertionError here and let the test pass even if egress
    # occurred. Recording invocations and asserting non-invocation afterward
    # catches that regardless of what upstream does with the exception.
    egress_spy = Mock(side_effect=AssertionError("network egress attempted"))
    monkeypatch.setattr(requests, "post", egress_spy)

    from api.main import _PUBLIC_THANKS

    client = _client()
    started = _start_public(client)
    session_id = started["session_id"]

    responses = _drive_public_to_completion(client, session_id)

    for response in responses:
        assert response.status_code == 200
    final_body = responses[-1].json()
    assert final_body == {"status": "complete", "message": _PUBLIC_THANKS}
    egress_spy.assert_not_called()

    conn = sqlite3.connect(db_path)
    try:
        task_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE session_id = ? AND tenant = ?",
            (session_id, _PUBLIC_TENANT),
        ).fetchone()[0]
        graph_count = conn.execute(
            "SELECT COUNT(*) FROM workflow_graphs WHERE session_id = ? AND tenant = ?",
            (session_id, _PUBLIC_TENANT),
        ).fetchone()[0]
        opportunity_count = conn.execute(
            "SELECT COUNT(*) FROM opportunities WHERE tenant = ?", (_PUBLIC_TENANT,)
        ).fetchone()[0]
        recommendation_count = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE tenant = ?", (_PUBLIC_TENANT,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert task_count >= 1
    assert graph_count == 1
    assert opportunity_count >= 1
    assert recommendation_count >= 1


# --- (b) Auth boundary on the review-path endpoints, under public-leads ---


def test_review_path_endpoints_still_require_auth_under_public_leads_tenant(monkeypatch, tmp_path):
    """G2/G4's isolation must not double as an accidental auth bypass: every
    authenticated endpoint the review path (A5) actually uses must 401 with
    no/garbage auth even when queried against tenant=public-leads, exactly
    as it would for any real tenant."""
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    started = _start_public(client)
    session_id = started["session_id"]

    repo = KBRepository(db_path)
    try:
        businesses = repo.list_businesses(_PUBLIC_TENANT)
    finally:
        repo.close()
    assert len(businesses) == 1
    business_id = businesses[0]["id"]

    no_auth_cases = [
        ("GET", "/businesses", {"tenant": _PUBLIC_TENANT}),
        ("GET", f"/businesses/{business_id}/sessions", {"tenant": _PUBLIC_TENANT}),
        ("GET", f"/interviews/{session_id}/transcript", {"tenant": _PUBLIC_TENANT}),
    ]
    for method, path, params in no_auth_cases:
        response = client.request(method, path, params=params)
        assert response.status_code == 401, f"{method} {path} without auth"

        garbage = client.request(
            method, path, params=params, headers={"Authorization": "Bearer nonsense"}
        )
        assert garbage.status_code == 401, f"{method} {path} with garbage auth"


# --- (c) Capability containment: public session_id vs. the operator route ---


def test_public_session_id_against_operator_answer_route_capability_containment(
    monkeypatch, tmp_path
):
    """A public session_id is a bare capability token, nothing more: the
    operator answer route still authenticates first regardless of tenant
    (no token -> 401); a legitimate operator with tenant=public-leads can
    take the lead over (intended behavior, D9 — not a hole); any other
    tenant 404s identically to an unknown id (G2's structural isolation, the
    same guarantee Item 2's own isolation test proves for the public
    route, checked here in the other direction)."""
    db_path = _set_env(monkeypatch, tmp_path)
    client = _client()
    started = _start_public(client)
    session_id = started["session_id"]

    no_token = client.post(
        f"/interviews/{session_id}/answer",
        params={"tenant": _PUBLIC_TENANT},
        json={"answer": "trying to hijack without a token"},
    )
    assert no_token.status_code == 401

    token = _login_token(client, db_path)

    legit_takeover = client.post(
        f"/interviews/{session_id}/answer",
        params={"tenant": _PUBLIC_TENANT},
        headers={"Authorization": f"Bearer {token}"},
        json={"answer": "operator legitimately taking over the lead"},
    )
    assert legit_takeover.status_code == 200
    assert legit_takeover.json()["session_id"] == session_id

    wrong_tenant = client.post(
        f"/interviews/{session_id}/answer",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
        json={"answer": "should not resolve under a different tenant"},
    )
    assert wrong_tenant.status_code == 404
    assert wrong_tenant.json() == {"detail": "not found"}


# --- (d) Flood / honeypot at volume ---


def test_public_start_flood_stops_row_growth_at_the_rate_limit(monkeypatch, tmp_path):
    """Driving the public start endpoint well past its configured per-minute
    limit must not grow the business row count beyond the limit — the
    limiter, not merely the daily cap, is what stops a burst flood."""
    db_path = _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "3")
    # Pin api/main.py's window clock (window = int(time.time() // 60)) to a
    # single fixed instant so the whole burst below lands in one window
    # regardless of which real wall-clock second the test happens to run on
    # — without this, a burst straddling a real minute boundary can land in
    # two windows and produce a nondeterministic status/row count.
    monkeypatch.setattr("api.main.time.time", lambda: 1_700_000_000.0)
    client = _client()

    responses = [
        client.post(
            "/public/intake",
            json={"business_name": f"Flood Lead {i}", "contact": f"flood{i}@example.com"},
        )
        for i in range(6)
    ]

    statuses = [response.status_code for response in responses]
    assert statuses[:3] == [200, 200, 200]
    assert all(status == 429 for status in statuses[3:])

    repo = KBRepository(db_path)
    try:
        assert len(repo.list_businesses(_PUBLIC_TENANT)) == 3
    finally:
        repo.close()


def test_public_start_honeypot_at_volume_writes_nothing(monkeypatch, tmp_path):
    """A honeypot-tripped flood — even a sizeable burst of it, and with the
    rate limit set generously high so the limiter itself can't be the
    reason nothing was written — must never create the database file at
    all: the honeypot check runs before any repo/db access is opened."""
    db_path = _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "50")
    client = _client()

    responses = [
        client.post(
            "/public/intake",
            json={
                "business_name": f"Bot Lead {i}",
                "contact": f"bot{i}@example.com",
                "website": "http://bot.example",
            },
        )
        for i in range(10)
    ]

    assert all(response.status_code == 200 for response in responses)
    assert not os.path.exists(db_path)


def test_public_answer_flood_gets_rate_limited_without_advancing_any_session(monkeypatch, tmp_path):
    """The answer endpoint shares the same disjoint public bucket (G5) as
    the start endpoint, not a separate/looser one: once the public limiter
    trips for a host, further answer attempts from that host 429 too, and
    no turn is written by a 429'd attempt."""
    db_path = _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROCESSFORGE_PUBLIC_RATE_LIMIT_PER_MINUTE", "1")
    # Same fixed-window pin as test_public_start_flood_stops_row_growth_at_the_rate_limit
    # above, for the same reason: keep the start call and the whole answer
    # flood in a single deterministic rate-limit window.
    monkeypatch.setattr("api.main.time.time", lambda: 1_700_000_000.0)
    client = _client()

    # The single start call consumes the window's only slot.
    started = _start_public(client)
    session_id = started["session_id"]

    flooded = [_answer_public(client, session_id, f"flood answer {i}") for i in range(5)]
    assert all(response.status_code == 429 for response in flooded)

    repo = KBRepository(db_path)
    try:
        turns = repo.list_turns(session_id)
    finally:
        repo.close()
    # Only the 3 turns seeded by the start call — no flooded 429'd attempt
    # added anything.
    assert len(turns) == 3
