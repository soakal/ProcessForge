"""Tests for POST /businesses/{business_id}/interviews: auth, identical 404 on
unknown-id/wrong-tenant (with no session or turn written in either case), the
happy-path session/turn shape (mirrors POST /interviews' own session block),
repeated calls bumping session_count without touching the first session, that
a session started here still gets the existing completed-session 409 on
POST /interviews/{session_id}/answer, and that a second full interview driven
to completion on an already-interviewed business yields its own independent
recommendation without disturbing the first session. Same TestClient/env-var
conventions as tests/test_get_business_sessions_api.py."""
from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from api.main import _INTERVIEW_OPENER
from auth.repository import AuthRepository
from kb.repository import KBRepository
from pipeline import _migrate


def _client():
    from api.main import app

    return TestClient(app)


def _set_env(monkeypatch, tmp_path, rate_limit=None):
    monkeypatch.setenv("PROCESSFORGE_DB_PATH", str(tmp_path / "test.db"))
    if rate_limit is not None:
        monkeypatch.setenv("PROCESSFORGE_RATE_LIMIT_PER_MINUTE", str(rate_limit))


def _seed_operator(db_path, username="alice", password="correct-horse-battery"):  # nosec B107 - test fixture only, not a real credential
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        repo.create_operator(username, password)
    finally:
        repo.close()


def _login_token(client, db_path, username="alice", password="correct-horse-battery"):  # nosec B107 - test fixture only, not a real credential
    _seed_operator(db_path, username=username, password=password)
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _new_id() -> str:
    return str(uuid.uuid4())


def _seed_bare_business(db_path, tenant="acme", name="Bare Co"):
    """Migrate the schema and create a business with no children directly via
    KBRepository, mirroring tests/test_get_business_sessions_api.py's own
    seeding style."""
    _migrate(db_path)
    repo = KBRepository(db_path)
    try:
        business_id = _new_id()
        repo.put("businesses", {
            "id": business_id, "schema_version": 1, "tenant": tenant,
            "name": name, "meta": {},
        })
        return business_id
    finally:
        repo.close()


def _start_business_interview(client, token, business_id, tenant):
    return client.post(
        f"/businesses/{business_id}/interviews",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_sessions(client, token, business_id, tenant):
    return client.get(
        f"/businesses/{business_id}/sessions",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_transcript(client, token, session_id, tenant):
    return client.get(
        f"/interviews/{session_id}/transcript",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
    )


def _answer_interview(client, token, session_id, answer, tenant):
    return client.post(
        f"/interviews/{session_id}/answer",
        params={"tenant": tenant},
        headers={"Authorization": f"Bearer {token}"},
        json={"answer": answer},
    )


# Same fixed 6-question ladder tests/test_api.py::
# test_interview_full_flow_completes_after_six_deterministic_answers relies on:
# tests/conftest.py's autouse _no_real_llm_provider fixture strips
# PROCESSFORGE_LLM_PROVIDER, so stages.interviewer.next_question always falls
# back to this deterministic ladder instead of calling out to an LLM.
_DETERMINISTIC_ANSWERS = [
    "We manually reconcile invoices every week.",
    "It takes about 2 hours each time.",
    "We'd like it automated so no one has to touch a spreadsheet.",
    "The files live in a shared network drive.",
    "Only rows where status is 'open'.",
    "An Excel spreadsheet.",
]


def _drive_interview_to_completion(client, token, session_id, tenant="acme"):
    """Answer the deterministic ladder above until the interview completes,
    and return the final completion response body (the same
    business_id/session_id/task_count/opportunities/recommendations shape
    POST /sessions returns)."""
    body = None
    for answer in _DETERMINISTIC_ANSWERS:
        response = _answer_interview(client, token, session_id, answer, tenant)
        assert response.status_code == 200
        body = response.json()
    assert "recommendations" in body, f"interview did not complete: {body}"
    return body


def test_start_business_interview_missing_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        f"/businesses/{_new_id()}/interviews", params={"tenant": "acme"}
    )

    assert response.status_code == 401


def test_start_business_interview_garbage_token_rejected(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path)
    client = _client()

    response = client.post(
        f"/businesses/{_new_id()}/interviews",
        params={"tenant": "acme"},
        headers={"Authorization": "Bearer garbage-token"},
    )

    assert response.status_code == 401


def test_start_business_interview_unknown_id_and_wrong_tenant_identical_404(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    real_business_id = _seed_bare_business(db_path, tenant="acme")

    unknown_response = _start_business_interview(client, token, _new_id(), tenant="acme")
    wrong_tenant_response = _start_business_interview(
        client, token, real_business_id, tenant="other-tenant"
    )

    assert unknown_response.status_code == 404
    assert wrong_tenant_response.status_code == 404
    assert unknown_response.json() == wrong_tenant_response.json()
    assert unknown_response.json() == {"detail": "not found"}

    # Neither call may have written a session or a turn.
    sessions_response = _get_sessions(client, token, real_business_id, tenant="acme")
    assert sessions_response.status_code == 200
    assert sessions_response.json() == []

    repo = KBRepository(db_path)
    try:
        assert repo.list_by_business("sessions", real_business_id, tenant="acme") == []
    finally:
        repo.close()


def test_start_business_interview_happy_path(monkeypatch, tmp_path):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme")

    response = _start_business_interview(client, token, business_id, tenant="acme")

    assert response.status_code == 200
    body = response.json()
    assert body["business_id"] == business_id
    assert body["question"] == _INTERVIEW_OPENER
    session_id = body["session_id"]

    sessions_response = _get_sessions(client, token, business_id, tenant="acme")
    assert sessions_response.status_code == 200
    sessions = sessions_response.json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id
    assert sessions[0]["status"] == "active"
    assert sessions[0]["started_at"] is not None

    repo = KBRepository(db_path)
    try:
        turns = repo.list_turns(session_id)
        assert len(turns) == 1
        assert turns[0]["role"] == "question"
        assert turns[0]["content"] == _INTERVIEW_OPENER
    finally:
        repo.close()

    # The same single "question" turn must also be visible via the transcript
    # endpoint the spec criterion actually names (GET /interviews/{id}/transcript).
    transcript_response = _get_transcript(client, token, session_id, tenant="acme")
    assert transcript_response.status_code == 200
    transcript = transcript_response.json()
    assert len(transcript) == 1
    assert transcript[0]["role"] == "question"
    assert transcript[0]["content"] == _INTERVIEW_OPENER


def test_start_business_interview_twice_bumps_session_count_first_session_untouched(
    monkeypatch, tmp_path
):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme")

    first_response = _start_business_interview(client, token, business_id, tenant="acme")
    assert first_response.status_code == 200
    first_session_id = first_response.json()["session_id"]

    second_response = _start_business_interview(client, token, business_id, tenant="acme")
    assert second_response.status_code == 200
    second_session_id = second_response.json()["session_id"]

    assert first_session_id != second_session_id

    businesses_response = client.get(
        "/businesses",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert businesses_response.status_code == 200
    business_rows = businesses_response.json()
    assert len(business_rows) == 1
    assert business_rows[0]["session_count"] == 2

    repo = KBRepository(db_path)
    try:
        first_turns = repo.list_turns(first_session_id)
        assert len(first_turns) == 1
        assert first_turns[0]["role"] == "question"
        assert first_turns[0]["content"] == _INTERVIEW_OPENER
    finally:
        repo.close()


def test_start_business_interview_session_completed_then_answer_returns_409(
    monkeypatch, tmp_path
):
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)
    business_id = _seed_bare_business(db_path, tenant="acme")

    start_response = _start_business_interview(client, token, business_id, tenant="acme")
    assert start_response.status_code == 200
    session_id = start_response.json()["session_id"]

    repo = KBRepository(db_path)
    try:
        repo.put("sessions", {
            "id": session_id, "schema_version": 1, "business_id": business_id,
            "status": "complete", "transcript_ref": session_id,
        })
    finally:
        repo.close()

    answer_response = client.post(
        f"/interviews/{session_id}/answer",
        params={"tenant": "acme"},
        json={"answer": "This should be rejected."},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert answer_response.status_code == 409


def test_start_business_interview_second_completed_session_gets_own_recommendation_first_untouched(
    monkeypatch, tmp_path
):
    """Load-bearing integration test (acceptance criterion 4): a business
    created via a completed first interview can be handed a second interview
    through POST /businesses/{business_id}/interviews, driven to its own
    completion, without disturbing the first session or creating a duplicate
    business row."""
    _set_env(monkeypatch, tmp_path, rate_limit=100)
    db_path = os.environ["PROCESSFORGE_DB_PATH"]
    client = _client()
    token = _login_token(client, db_path)

    # First business + session created the normal way (POST /interviews, not
    # a direct repo.put), then driven all the way to completion.
    first_started = client.post(
        "/interviews",
        headers={"Authorization": f"Bearer {token}"},
        json={"business_name": "Acme Co", "tenant": "acme"},
    )
    assert first_started.status_code == 200
    first_started_body = first_started.json()
    business_id = first_started_body["business_id"]
    first_session_id = first_started_body["session_id"]

    first_completion = _drive_interview_to_completion(client, token, first_session_id, tenant="acme")
    first_recommendation = first_completion["recommendations"][0]

    first_transcript_response = _get_transcript(client, token, first_session_id, tenant="acme")
    assert first_transcript_response.status_code == 200
    first_transcript_before = first_transcript_response.json()

    # Second interview on the SAME business via the endpoint under test.
    second_started = _start_business_interview(client, token, business_id, tenant="acme")
    assert second_started.status_code == 200
    second_session_id = second_started.json()["session_id"]
    assert second_session_id != first_session_id

    second_completion = _drive_interview_to_completion(client, token, second_session_id, tenant="acme")
    second_recommendation = second_completion["recommendations"][0]

    # The second session yields its own, distinct completion — not a reuse of
    # the first session's recommendation.
    assert second_completion["session_id"] != first_completion["session_id"]
    assert second_recommendation["id"] != first_recommendation["id"]

    # session_count reflects both sessions on the single business row — no
    # duplicate business row was created.
    businesses_response = client.get(
        "/businesses",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert businesses_response.status_code == 200
    business_rows = businesses_response.json()
    assert len(business_rows) == 1
    assert business_rows[0]["session_count"] == 2

    # The first session's status, transcript turns, and recommendation are
    # unchanged after the second interview completes.
    repo = KBRepository(db_path)
    try:
        first_session_row = repo.get("sessions", first_session_id, "acme")
    finally:
        repo.close()
    assert first_session_row["status"] == "complete"

    first_transcript_after = _get_transcript(client, token, first_session_id, tenant="acme")
    assert first_transcript_after.status_code == 200
    assert first_transcript_after.json() == first_transcript_before

    first_recommendation_after = client.get(
        f"/recommendations/{first_recommendation['id']}",
        params={"tenant": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first_recommendation_after.status_code == 200
    # Compare the fields that are set directly from the persisted Recommendation
    # record (not session_id/roi_*, which get_recommendation resolves fresh on
    # every call via _resolve_session_id/_resolve_roi and are never populated on
    # the recommendation embedded in an interview-completion response — see
    # tests/test_api.py::test_get_recommendation_includes_session_id_when_resolvable).
    first_recommendation_after_body = first_recommendation_after.json()
    assert first_recommendation_after_body["id"] == first_recommendation["id"]
    assert first_recommendation_after_body["opportunity_id"] == first_recommendation["opportunity_id"]
    assert first_recommendation_after_body["summary"] == first_recommendation["summary"]
    assert first_recommendation_after_body["approval_state"] == first_recommendation["approval_state"]
