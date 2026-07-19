"""UI layer: /ui/login and its static assets — no auth required for any of
these, since the login page and its assets must be reachable before a token
exists."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient


def _client():
    from api.main import app

    return TestClient(app)


def test_ui_login_renders_form():
    client = _client()
    response = client.get("/ui/login")
    assert response.status_code == 200
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
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


def test_ui_static_css_mobile_friendly_foundation():
    # Item 1 of docs/FEATURE-SPEC-mobile-friendly.md: nav wrap/spacing, touch
    # targets, and textarea styling. The repo's single breakpoint convention
    # is @media (max-width: 640px) — asserted here so no other breakpoint
    # value can silently slip into an @media rule later.
    client = _client()
    response = client.get("/ui/static/app.css")
    assert response.status_code == 200
    css = response.text
    assert "(max-width: 640px)" in css
    media_queries = re.findall(r"@media\s*\(([^)]*)\)", css)
    assert media_queries, "expected at least one @media rule"
    assert all(query.strip() == "max-width: 640px" for query in media_queries)
    nav_rule = re.search(r"\.nav\s*\{[^}]*\}", css)
    assert nav_rule is not None
    assert "flex-wrap: wrap" in nav_rule.group(0)
    assert "min-height: 44px" in css
    textarea_rule = re.search(r"textarea\s*\{[^}]*\}", css)
    assert textarea_rule is not None
    assert "font-size: 1rem" in textarea_rule.group(0)


def test_ui_static_css_audit_log_horizontal_scroll():
    # Item 3 of docs/FEATURE-SPEC-mobile-friendly.md: the 7-column forensic
    # audit-log table gets an in-container horizontal-scroll wrapper so the
    # page body itself never pans sideways at narrow viewport widths.
    client = _client()
    response = client.get("/ui/static/app.css")
    assert response.status_code == 200
    css = response.text
    assert "#audit-log-results" in css
    assert "overflow-x: auto" in css


def test_ui_static_css_businesses_stacked_table():
    # Item 4 of docs/FEATURE-SPEC-mobile-friendly.md: businesses table gets
    # card-stacked rows at <=640px via table.stacked + td::before labels,
    # reusing the repo's single (max-width: 640px) breakpoint.
    client = _client()
    response = client.get("/ui/static/app.css")
    assert response.status_code == 200
    css = response.text
    media_match = re.search(r"@media\s*\(max-width:\s*640px\)\s*\{(.*)\}\s*$", css, re.DOTALL)
    assert media_match is not None
    mobile_block = media_match.group(1)
    assert "table.stacked" in mobile_block
    assert "content: attr(data-label)" in mobile_block
    # Item 5 of docs/FEATURE-SPEC-mobile-friendly.md: the operators table
    # reuses the same table.stacked rules, extended to cover password inputs
    # (the businesses table only ever has text inputs) rather than
    # duplicating a second breakpoint/selector set.
    assert 'table.stacked td input[type="password"]' in mobile_block


def test_ui_static_js_served():
    client = _client()
    response = client.get("/ui/static/app.js")
    assert response.status_code == 200


def test_ui_dashboard_renders_form():
    client = _client()
    response = client.get("/ui")
    assert response.status_code == 200
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
    assert "business_name" in response.text
    assert "tenant" in response.text
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "turning a business's manual process into an automation recommendation" in response.text
    assert 'class="next-step"' in response.text
    assert "fill in the business name and tenant below" in response.text
    # Item 15: read-only "Your Businesses & Past Interviews" section below
    # the start form — auto-fetches when a tenant is remembered, shows a
    # plain hint when it isn't, and always links to /ui/businesses.
    assert "Your Businesses &amp; Past Interviews" in response.text
    assert "requireAuth" in response.text
    assert "fetchWithAuth" in response.text
    assert '"/businesses?tenant=" + encodeURIComponent(dashboardLastTenant)' in response.text
    assert 'localStorage.getItem("pf_last_tenant")' in response.text
    assert "No tenant remembered yet." in response.text
    assert '<a href="/ui/businesses">Manage businesses</a>' in response.text
    assert 'link.href = "/ui/businesses";' in response.text
    assert "No businesses found for this tenant yet." in response.text
    assert "innerHTML" not in response.text
    # Starting an interview stores pf_last_tenant so this section (and
    # /ui/businesses) can pick it up next visit.
    assert 'localStorage.setItem("pf_last_tenant", tenant);' in response.text


def test_ui_interview_renders_page():
    client = _client()
    response = client.get("/ui/interview")
    assert response.status_code == 200
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
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
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
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
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
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
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
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
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
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
    # Item 12: inline rename — Save posts to the edit endpoint, a per-row
    # error branch exists, and the page stays innerHTML-free (checked above).
    assert '"/businesses/" + encodeURIComponent(business.id) + "/edit?tenant=" + encodeURIComponent(tenant)' in response.text
    assert "Rename" in response.text
    assert "Business not found." in response.text
    assert "Name must be between 1 and 500 characters." in response.text
    # Item 13a: per-session inline delete — strict !== confirm guard before
    # the fetch fires, and the /sessions/{sid}/delete URL pattern.
    assert "confirmInput.value !== session.id" in response.text
    assert (
        '"/sessions/" + encodeURIComponent(session.id) + "/delete?tenant=" + encodeURIComponent(tenant)'
        in response.text
    )
    assert "confirm_session_id" in response.text
    assert "The confirmation doesn't match the session ID." in response.text
    # Item 13b: per-business delete deep-link to the dedicated confirm page,
    # carrying business_id/tenant as encoded query params.
    assert '"/ui/businesses/delete?business_id=" +' in response.text
    assert '"&tenant=" +' in response.text
    # Item 14: Resume an active interview — only rendered for
    # status==="active" sessions, fetches the transcript, picks the last
    # question turn, writes pf_interview_state, and navigates to
    # /ui/interview. No question turn found -> per-row error, no navigation.
    assert 'session.status === "active"' in response.text
    assert (
        '"/interviews/" + encodeURIComponent(session.id) + "/transcript?tenant=" + encodeURIComponent(tenant)'
        in response.text
    )
    assert 'turn.role === "question"' in response.text
    assert "This interview has no question to resume from." in response.text
    assert "pf_interview_state" in response.text
    assert 'window.location.href = "/ui/interview";' in response.text
    # Item 4 of docs/FEATURE-SPEC-mobile-friendly.md: card-stacking the
    # businesses table for <=640px viewports — dataset.label on each td plus
    # table.className = "stacked", attribute-only (innerHTML stays absent,
    # asserted above).
    assert 'table.className = "stacked"' in response.text
    assert 'nameCell.dataset.label = "Name"' in response.text
    assert 'idCell.dataset.label = "ID"' in response.text
    assert 'countCell.dataset.label = "Sessions"' in response.text
    assert 'actionsCell.dataset.label = "Actions"' in response.text


def test_ui_businesses_delete_renders_form():
    client = _client()
    response = client.get("/ui/businesses/delete")
    assert response.status_code == 200
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
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
    assert "innerHTML" not in response.text
    # Item 13c: business_id/tenant are prefilled from the incoming
    # ?business_id=&tenant= deep-link via URLSearchParams; confirm_business_id
    # is provably never touched by that prefill code.
    assert "URLSearchParams" in response.text
    assert 'document.getElementById("business_id").value = businessIdParam;' in response.text
    assert 'document.getElementById("tenant").value = tenantParam;' in response.text
    assert 'getElementById("confirm_business_id").value =' not in response.text


def test_ui_operators_renders_form():
    client = _client()
    response = client.get("/ui/operators")
    assert response.status_code == 200
    # Item 2 of docs/FEATURE-SPEC-mobile-friendly.md: locks in the viewport
    # meta so a future refactor can't silently drop it.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
    # Plain-language intro/next-step copy: locks in that the page states what
    # it's for and what to do next, so a future edit can't silently drop it.
    assert 'class="page-intro"' in response.text
    assert "add a new one, reset a password, or remove an operator" in response.text
    assert 'class="next-step"' in response.text
    assert "review the operators below" in response.text
    # Shared client-side auth/fetch helpers, per Item 18's acceptance
    # criteria.
    assert "requireAuth" in response.text
    assert "fetchWithAuth" in response.text
    assert "innerHTML" not in response.text
    # Item 19 (folded into this cycle): the nav gets a new Operators link
    # alongside Businesses, mirroring Item 10's nav-link assertion.
    assert '<a href="/ui/operators">Operators</a>' in response.text
    # Operator table sourced from GET /auth/operators, sorted client-side.
    assert '"/auth/operators"' in response.text
    assert "localeCompare" in response.text
    assert "No operators found." in response.text
    # Add-operator form: username + password + confirm-password, with a
    # strict client-side match guard that runs BEFORE any fetch call.
    assert "new_username" in response.text
    assert "new_password_confirm" in response.text
    add_operator_guard = response.text.index("if (password !== confirmPassword)")
    add_operator_fetch = response.text.index('fetchWithAuth("/auth/operators", {')
    assert add_operator_guard < add_operator_fetch
    assert "The passwords don't match." in response.text
    # Both password fields are cleared after every add-operator submit,
    # success or failure (G7 — passwords never echoed/retained).
    assert 'passwordInput.value = "";' in response.text
    assert 'confirmInput.value = "";' in response.text
    # Per-row Reset Password: new + confirm fields, same strict match guard
    # before its own fetch.
    reset_guard = response.text.index("if (newPassword !== confirmPassword)")
    reset_fetch = response.text.index('fetchWithAuth("/auth/operators/password"')
    assert reset_guard < reset_fetch
    # Per-row Delete: retype-username confirm, strict !== guard before its
    # own fetch.
    delete_guard = response.text.index("if (confirmInput.value !== operator.username)")
    delete_fetch = response.text.index('fetchWithAuth("/auth/operators/delete"')
    assert delete_guard < delete_fetch
    assert "The confirmation doesn't match the username." in response.text
    # Own-row Delete control is suppressed entirely (UX only — the server
    # still enforces the self-delete 409 regardless, D6).
    assert "const isSelf = operator.username === currentUsername;" in response.text
    assert "if (!isSelf) {" in response.text
    # Self password change: clears pf_token/pf_username and redirects to
    # /ui/login (D9), with a pre-submit warning note.
    assert "changing your own password signs you out" in response.text
    assert 'localStorage.removeItem("pf_token");' in response.text
    assert 'localStorage.removeItem("pf_username");' in response.text
    assert 'window.location.href = "/ui/login";' in response.text
    # Item 5 of docs/FEATURE-SPEC-mobile-friendly.md: card-stacking the
    # operators table for <=640px viewports — dataset.label on each td plus
    # table.className = "stacked", reusing Item 4's shared table.stacked
    # rules (attribute-only, innerHTML stays absent, asserted above).
    assert 'table.className = "stacked"' in response.text
    assert 'usernameCell.dataset.label = "Username"' in response.text
    assert 'createdCell.dataset.label = "Created"' in response.text
    assert 'actionsCell.dataset.label = "Actions"' in response.text
