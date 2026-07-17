"""UI layer: /ui/login and its static assets — no auth required for any of
these, since the login page and its assets must be reachable before a token
exists."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from api.main import app

    return TestClient(app)


def test_ui_login_renders_form():
    client = _client()
    response = client.get("/ui/login")
    assert response.status_code == 200
    assert "username" in response.text
    assert "password" in response.text


def test_ui_static_css_served():
    client = _client()
    response = client.get("/ui/static/app.css")
    assert response.status_code == 200
    assert "css" in response.headers["content-type"]


def test_ui_static_js_served():
    client = _client()
    response = client.get("/ui/static/app.js")
    assert response.status_code == 200


def test_ui_dashboard_renders_form():
    client = _client()
    response = client.get("/ui")
    assert response.status_code == 200
    assert "business_name" in response.text
    assert "tenant" in response.text


def test_ui_interview_renders_page():
    client = _client()
    response = client.get("/ui/interview")
    assert response.status_code == 200
    # TestClient's GET carries no browser sessionStorage, so the JS-driven
    # question/answer flow never runs here — this only confirms the page
    # structure (including the "no interview in progress" fallback text
    # that renders when pf_interview_state is absent) is present in the
    # rendered HTML.
    assert "No interview in progress" in response.text


def test_ui_recommendation_renders_page():
    client = _client()
    response = client.get("/ui/recommendations/some-fake-id")
    assert response.status_code == 200
    # TestClient's GET never executes the inline fetch-on-load script against
    # a real backend recommendation, so this only confirms the static page
    # structure (including the Approve/Build controls and the recommendation
    # id embedded for the client-side script) is present in the rendered
    # HTML.
    assert "Approve" in response.text
    assert "Build" in response.text
    assert "some-fake-id" in response.text


def test_ui_audit_log_renders_form():
    client = _client()
    response = client.get("/ui/audit-log")
    assert response.status_code == 200
    assert "tenant" in response.text
    assert "record_id" in response.text
    assert "Search" in response.text


def test_ui_businesses_delete_renders_form():
    client = _client()
    response = client.get("/ui/businesses/delete")
    assert response.status_code == 200
    assert "confirm" in response.text.lower()
    assert "cannot be undone" in response.text.lower()
