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
