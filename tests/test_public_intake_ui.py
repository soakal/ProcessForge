"""Public lead intake feature (docs/FEATURE-SPEC-public-lead-intake.md),
Item 3: GET /public/intake — the self-contained public page. No auth
required; the template must not extend base.html or reference any operator
plumbing (app.js/app.css/fetchWithAuth/requireAuth/pf_token/href="/ui), and
the repo-wide innerHTML-free discipline (G6) must hold across all of
web/templates, this new one included."""
from __future__ import annotations

import pathlib

from fastapi.testclient import TestClient


def _client():
    from api.main import app

    return TestClient(app)


def test_public_intake_page_renders_no_auth():
    client = _client()
    response = client.get("/public/intake")
    assert response.status_code == 200

    text = response.text
    assert "Tell Us About a Process You'd Like Automated" in text
    # The three step-1 fields (D4: contact collected up front alongside the
    # business name; honeypot is the third, D6).
    assert 'id="business_name"' in text
    assert 'id="contact"' in text
    assert 'id="website"' in text
    assert '<meta name="robots" content="noindex">' in text
    assert "Powered by CwiAI" in text
    # Honeypot hiding class (D8/Item 3b): not type="hidden" (bots skip that).
    assert 'class="hp-field"' in text
    assert 'type="text" id="website"' in text


def test_public_intake_page_no_auth_header_required():
    # G2/D9: unauthenticated by design — a garbage Authorization header
    # changes nothing, same as the POST endpoints in Items 1-2.
    client = _client()
    response = client.get(
        "/public/intake", headers={"Authorization": "Bearer nonsense"}
    )
    assert response.status_code == 200


def test_public_intake_page_has_no_operator_plumbing():
    # D8: fully self-contained — no base.html, no app.js/app.css, no link
    # back into the authenticated /ui surface, no innerHTML, no reference to
    # the operator's stored token.
    client = _client()
    response = client.get("/public/intake")
    text = response.text
    for forbidden in (
        "{% extends",
        "app.js",
        "app.css",
        "fetchWithAuth",
        "requireAuth",
        'href="/ui',
        "innerHTML",
        "pf_token",
    ):
        assert forbidden not in text, f"unexpected {forbidden!r} in public_intake.html"


def test_public_intake_page_script_flow():
    # Item 3c: plain-fetch step-1 -> Q&A -> thank-you flow, with disabled
    # handling and a maxlength on the answer field.
    client = _client()
    response = client.get("/public/intake")
    text = response.text
    assert '"/public/intake"' in text
    assert "/public/intake/" in text
    assert "/answer" in text
    assert 'data.status === "complete"' in text
    assert ".disabled = true" in text
    assert ".disabled = false" in text
    assert 'maxlength="4000"' in text


def test_innerhtml_absent_repo_wide_in_templates():
    # G6: repo-wide grep across web/templates stays at zero matches,
    # including this new template.
    templates_dir = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
    offenders = []
    for path in templates_dir.glob("*.html"):
        if "innerHTML" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []
