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
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "Log in with your operator account" in response.text
    assert 'class="next-step"' in response.text
    assert "enter your username and password below" in response.text


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
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "turning a business's manual process into an automation recommendation" in response.text
    assert 'class="next-step"' in response.text
    assert "fill in the business name and tenant below" in response.text


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
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "interview questions ProcessForge uses to learn about your business process" in response.text
    assert 'class="next-step"' in response.text
    assert "read the question below, type your answer" in response.text


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
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for, and shows ROI + status prominently with a per-state next-step
    # message (draft->approve, approved->build, built->feedback).
    assert 'class="page-intro"' in response.text
    assert "estimated time savings (ROI) and current status" in response.text
    assert 'class="next-step"' in response.text
    assert 'class="status-line"' in response.text
    assert 'class="roi-line"' in response.text
    assert "review the ROI and summary above" in response.text
    assert "to generate the automation" in response.text
    assert "submit feedback if changes are needed" in response.text


def test_ui_interview_transcript_renders_page():
    client = _client()
    response = client.get("/ui/interview/some-fake-id/transcript")
    assert response.status_code == 200
    # TestClient's GET never executes the inline fetch-on-load script against
    # a real backend transcript, so this only confirms the static page
    # structure is present: the session id embedded for the client-side
    # script, and the turn_index-based comparator that guarantees turns are
    # sorted into order once fetched (renderTurns() sorts by turn_index
    # before appending any DOM nodes).
    assert "some-fake-id" in response.text
    assert "turn_index" in response.text
    assert "sort(" in response.text
    assert "innerHTML" not in response.text
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "full conversation from an interview" in response.text
    assert 'class="next-step"' in response.text
    assert "return to the dashboard when you're done" in response.text


def test_ui_recommendation_renders_product_link_code():
    client = _client()
    response = client.get("/ui/recommendations/some-fake-id")
    assert response.status_code == 200
    # TestClient's GET never executes the inline fetch-on-load script against
    # a real backend automation, so this only confirms the static page
    # structure is present: the renderProduct() function that builds the
    # visible product link, the container/element ids it targets, and the
    # createElement("a") + .href assignment that make up the scheme-checked
    # visible-link code path (isSafeHttpUrl gates a real <a href> against a
    # textContent fallback).
    assert "renderProduct" in response.text
    assert "automation-product-link" in response.text
    assert 'createElement("a")' in response.text
    assert "isSafeHttpUrl" in response.text
    assert ".href = productUrl" in response.text
    assert "innerHTML" not in response.text


def test_ui_recommendation_renders_transcript_link_code():
    client = _client()
    response = client.get("/ui/recommendations/some-fake-id")
    assert response.status_code == 200
    # TestClient's GET never executes the inline fetch-on-load script against
    # a real backend recommendation, so this only confirms the static page
    # structure is present: the hidden-by-default container, the
    # renderTranscriptLink() function that builds the visible "View interview
    # transcript" link only when recommendation.session_id is present, and
    # the createElement("a") + textContent-only construction (no innerHTML).
    assert "renderTranscriptLink" in response.text
    assert "recommendation-transcript-link" in response.text
    assert "View interview transcript" in response.text
    assert "/ui/interview/" in response.text
    assert "/transcript?tenant=" in response.text
    assert 'createElement("a")' in response.text
    assert "innerHTML" not in response.text


def test_ui_recommendation_renders_roi_code():
    client = _client()
    response = client.get("/ui/recommendations/some-fake-id")
    assert response.status_code == 200
    # TestClient's GET never executes the inline fetch-on-load script against
    # a real backend recommendation, so this only confirms the static page
    # structure is present: the hidden-by-default container, the renderRoi()
    # function that renders roi_low_hrs/roi_high_hrs prominently only when
    # both are present (None-safe on the frontend, matching the backend's own
    # None-safe _resolve_roi()), and that it's built without innerHTML.
    assert "renderRoi" in response.text
    assert "recommendation-roi" in response.text
    assert "roi_low_hrs" in response.text
    assert "roi_high_hrs" in response.text
    assert "Estimated savings" in response.text
    assert "innerHTML" not in response.text


def test_ui_audit_log_renders_form():
    client = _client()
    response = client.get("/ui/audit-log")
    assert response.status_code == 200
    assert "tenant" in response.text
    assert "record_id" in response.text
    assert "Search" in response.text
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "every recorded approval-state change for a tenant" in response.text
    assert 'class="next-step"' in response.text
    assert 'enter a tenant below' in response.text


def test_ui_businesses_renders_form():
    client = _client()
    response = client.get("/ui/businesses")
    assert response.status_code == 200
    assert "tenant" in response.text
    assert "Load" in response.text
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "how many interview sessions it has" in response.text
    assert 'class="next-step"' in response.text
    assert 'enter a tenant below' in response.text
    # Shared client-side auth/fetch helpers and the tenant-persistence key
    # this page introduces.
    assert "requireAuth" in response.text
    assert "fetchWithAuth" in response.text
    assert "pf_last_tenant" in response.text
    assert "innerHTML" not in response.text
    # Nav now points to the new Businesses page instead of the old
    # Delete-Business shortcut (the delete route/page itself stays alive,
    # reached via per-row deep-links in a later cycle).
    assert '<a href="/ui/businesses">Businesses</a>' in response.text
    assert '<a href="/ui/businesses/delete">Delete Business</a>' not in response.text
    # Item 11: per-business Sessions expansion — fetch URL, link
    # construction, and empty-state text must all be present in the
    # rendered script, and the page must stay innerHTML-free.
    assert '"/businesses/" + encodeURIComponent(business.id) + "/sessions?tenant=" + encodeURIComponent(tenant)' in response.text
    assert '"/ui/interview/" +' in response.text
    assert '"/transcript?tenant="' in response.text
    assert '"/ui/recommendations/" +' in response.text
    assert "View recommendation" in response.text
    assert "No interviews yet." in response.text


def test_ui_businesses_delete_renders_form():
    client = _client()
    response = client.get("/ui/businesses/delete")
    assert response.status_code == 200
    assert "confirm" in response.text.lower()
    assert "cannot be undone" in response.text.lower()
    # Plain-language intro/next-step copy: the next-step line on this
    # destructive-action page is CAUTION-framed, not a generic nudge, and
    # must not replace the existing warning paragraph above the form.
    assert 'class="page-intro"' in response.text
    assert "permanently removes a business" in response.text
    assert 'class="next-step"' in response.text
    assert "double-check the business ID before deleting" in response.text
    assert "this action cannot be undone" in response.text
