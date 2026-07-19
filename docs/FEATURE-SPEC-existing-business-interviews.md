# ProcessForge: Businesses Auto-Load + Interviews for Existing Businesses — Implementation Spec

**Planned by Fable, 2026-07-19.** Follows the conventions established in `docs/FEATURE-SPEC-dashboard-and-users.md` and `docs/FEATURE-SPEC-mobile-friendly.md`. Council-loop's Arbiter decomposes into cycles — one item ≈ one cycle.

---

## Part A — Investigation findings (all verified against actual code)

### A1. Ask #1 — Businesses page does not auto-load (confirmed)

- `web/templates/businesses.html` prefills the tenant input from `localStorage.pf_last_tenant`, but the `GET /businesses?tenant=` fetch fires **only** inside the form's `submit` listener. Nothing calls it on page load.
- `web/templates/dashboard.html` (first spec's Item 15) is the established auto-fetch precedent: read `pf_last_tenant`, and if present run an async fetch and render — with all errors swallowed quietly, **because on the dashboard the section is a convenience that must never block the start-interview form**. That quiet-swallow rationale does NOT transfer to `/ui/businesses`, where the list *is* the page — errors there should surface in the existing `#businesses-error` box.
- The page's static next-step copy ("enter a tenant below, then select Load") is string-asserted by `tests/test_ui.py::test_ui_businesses_renders_form` — changing it requires updating that assertion in the same cycle.

### A2. Ask #2 — every interview creates a brand-new Business (confirmed)

- `api/main.py`'s `start_interview` unconditionally does `Business(id=str(uuid.uuid4()), tenant=body.tenant, name=body.business_name)` → `sink.save(business, ctx)`, then creates the Session, seeds the fixed `_INTERVIEW_OPENER` question turn, returns `{business_id, session_id, question}`. **No code path anywhere starts a session against an existing `business_id`.** Two interviews typed as "dogsrus" become two unrelated Business rows.
- **Multiple sessions per business are already fully supported and tested** — this is the load-bearing confirmation for the whole feature:
  - `Session` contract is just `id/business_id/status/transcript_ref` — nothing constrains one session per business.
  - `KBRepository.list_by_business("sessions", ...)` returns all sessions for a business; `list_businesses` computes an n-valued `session_count` subquery.
  - Existing tests already exercise plural sessions under one business. The data model needs **zero** changes; only the creation path is missing.
- **Zero repo changes needed for the new endpoint.** Everything it needs already exists: `repo.get("businesses", id, tenant)` (tenant-scoped existence check), `KBSink().save(session, ctx)`, `repo.add_turn(session_id, "question", _INTERVIEW_OPENER)`.
- **Tenant-passing convention for business-scoped routes is the query param**, not the body: `GET /businesses/{id}/sessions?tenant=`, `POST /businesses/{id}/edit?tenant=`, `POST /sessions/{id}/delete?tenant=` all take `tenant` as a query param. Only the business-*creating* `POST /interviews` takes tenant in the body (it has no parent to scope by). The new endpoint follows the query-param siblings.
- `interview.html`'s guard requires only `state.session_id` and `state.question` — `business_id` is optional (already relied on by Item 14's Resume flow). So a `pf_interview_state` of `{session_id, business_id, tenant, question}` written by a new businesses-page action reuses **`interview.html` completely unchanged**.
- The answer flow (`answer_interview`) keys everything off `session_id` + tenant-scoped session lookup; it is completely indifferent to *how* the session was created. No changes needed there either.

### A3. Ask #2b — "reopen a completed session" investigated and ruled out (Part D #4)

- `answer_interview` already returns `409 "interview already complete"` for any non-`active` session — a deliberate, tested gate.
- A completed session's transcript has already been flattened into derived Tasks → Opportunities → Recommendations at completion time. Appending turns afterward would desync the transcript from its derived records in exactly the way the first spec's D3 ruled out for retro-editing turns.
- The sanctioned post-hoc path already exists: `POST /recommendations/{id}/refine` appends question/answer turns and regenerates the handoff as a new Automation revision. Brian's actual phrasing — "add another process" — is a *new* problem, which is a new session, not an amendment to an old one.

### A4. Conventions inventory (for the Arbiter)

- Endpoint boilerplate: rate-limit-before-auth → `PROCESSFORGE_DB_PATH` from env → `_authenticate()` → `_open_repo()` with `close()` in `finally` (every endpoint).
- Test layout: feature-specific API test files are the norm (`tests/test_get_business_sessions_api.py`, `tests/test_edit_business_api.py`, ...); UI tests are string assertions in `tests/test_ui.py`. `tests/conftest.py`'s autouse LLM-provider-stripping fixture must not be weakened.
- Mobile spec's constraints carry forward: any new `<td>`s need `dataset.label`; the single breakpoint is `(max-width: 640px)`; zero `innerHTML`.

---

## Part B — Design decisions (with justification)

**D1 — Businesses page auto-load: same trigger as dashboard Item 15, different error posture.** On load, if `pf_last_tenant` exists, fetch `GET /businesses?tenant=` and render immediately — but implement it by factoring the existing submit-handler body into a shared `loadBusinesses(tenant)` function called from both the submit listener and the on-load path, so there is exactly one fetch/render/error code path. Errors surface in the existing `#businesses-error` box even on auto-load: unlike the dashboard, this page has no higher-priority element the error could block. The form stays fully functional for switching tenants.

**D2 — New endpoint: `POST /businesses/{business_id}/interviews?tenant=<t>`, no request body.** Nested under the business like its siblings (`/sessions`, `/edit`, `/delete`), tenant as query param per the business-scoped convention. No body: the business's name is already known and the opener is fixed. Returns the exact `{business_id, session_id, question}` shape `POST /interviews` returns, so the client-side handoff to `interview.html` is byte-compatible. Resolves the business tenant-scoped **before** any write (identical 404 for unknown-id/wrong-tenant).

**D3 — `POST /interviews` stays completely untouched, and the dashboard start form gains no existing-business picker.** The original endpoint is still the right tool for a genuinely new business. `/ui/businesses` is the single entry point for the new action.

**D4 — "Ask more questions" = a wholly NEW session (clean slate), never reopening a completed one.** Justified in A3 against the first spec's D3 precedent — this spec explicitly does not contradict it. New session → its own turns → at completion its own Tasks/Opportunities/Recommendations; prior sessions, approvals, and automations of the same business are untouched by construction.

**D5 — Duplicate business-name cleanup is out of scope (Part E).** This feature removes the *cause* of future accidental duplicates, but merging/deduping already-existing duplicate rows is a data-migration feature with hairy semantics Brian didn't ask for. Rename + Delete already exist for manual cleanup.

**D6 — No `Session` contract change; no per-session "topic" label.** Frozen contract stays frozen. If telling sessions apart by topic becomes a real pain, that's a future contract conversation.

**D7 — The fixed `_INTERVIEW_OPENER` is reused verbatim for the new endpoint.** Its wording is process-scoped, not business-scoped — fits a second process at a known business exactly as well as a first. Not worth an LLM call in a deterministic start endpoint.

**D8 — No guard against multiple concurrent `active` sessions per business.** Two in-flight interviews about two different processes is a legitimate state, already representable (each session row gets its own Resume button).

---

## Part C — Numbered implementation spec

**Global constraints binding every item (Arbiter: violations are automatic REVISE):**
- **G1** New endpoint copies the existing boilerplate exactly: rate-limit-before-auth → `PROCESSFORGE_DB_PATH` from env → `_authenticate()` → `_open_repo()` with `repo.close()` in `finally`.
- **G2** Tenant isolation: business resolved via `repo.get("businesses", business_id, tenant)` **before** any turn write or session save; unknown id and wrong tenant produce the identical `404 "not found"` (same status AND body).
- **G3** No changes to `contracts/records.py`, `kb/repository.py`, any migration, `stages/*`, `POST /interviews`, `answer_interview`, or `interview.html`. No new dependencies.
- **G4** Templates: `requireAuth()` first, all API calls via `fetchWithAuth`, all dynamic DOM via `createElement`/`textContent` — the repo-wide `innerHTML` grep across `web/templates` must stay at zero matches. Any new `<td>` gets a `dataset.label`; no new `@media` breakpoint other than `(max-width: 640px)`; no new CSS at all expected.
- **G5** Every cycle lands its tests in the same commit; `.\run-tests.ps1` green per cycle. All existing string assertions in `tests/test_ui.py` must keep passing except where an item explicitly says a copy change requires updating one.
- **G6** `USER_MANUAL.md` and `CLAUDE.md` Status updated in the same change as any user-facing behavior change.

### Item 1 — `/ui/businesses` auto-loads when a tenant is remembered

**Changes, all in `web/templates/businesses.html` + `tests/test_ui.py`:**
a. Factor the body of the existing `submit` listener (clear error/results, `localStorage.setItem("pf_last_tenant", ...)`, fetch `GET /businesses?tenant=`, 401/error branches, `renderResults`) into a single `async function loadBusinesses(tenant)`. The submit listener becomes `event.preventDefault();` + `loadBusinesses(tenantInput.value)`.
b. Directly after the existing prefill block, if `lastTenant` is truthy, call `loadBusinesses(lastTenant)` — errors surface in `#businesses-error` exactly as a manual Load would (D1).
c. Update the static next-step copy to cover both states, e.g.: `Next step: your last tenant's businesses load automatically — or enter a different tenant and select "Load".`
d. `tests/test_ui.py`: update `test_ui_businesses_renders_form`'s copy assertion to the new text; add assertions that the page script contains `function loadBusinesses` (or the chosen equivalent), an on-load invocation guarded by the remembered tenant, and still contains `pf_last_tenant`/`fetchWithAuth`/`requireAuth`.

**Acceptance criteria:**
1. Page still renders 200 with the tenant input, Load button, `.page-intro`, and updated `.next-step`.
2. Script contains exactly one fetch-and-render path, invoked from both submit and on-load; the on-load call is conditional on a remembered tenant (no fetch attempt when `pf_last_tenant` is absent).
3. `localStorage.pf_last_tenant` prefill behavior unchanged; manual Load with a different tenant still works and re-persists it.
4. `innerHTML` grep across `web/templates` still zero; all other existing businesses-page assertions pass unchanged.
5. `.\run-tests.ps1` green.

### Item 2 — API: `POST /businesses/{business_id}/interviews` (new session under an existing business)

**Changes: `api/main.py` + new `tests/test_start_business_interview_api.py`:**
a. New endpoint `POST /businesses/{business_id}/interviews`, params `business_id` (path), `tenant` (query), no request body. G1 boilerplate.
b. Handler: `repo.get("businesses", business_id, tenant)` → `None` ⇒ `HTTPException(404, "not found")` (G2, with a comment noting this must precede `add_turn` because turns are not tenant-scoped). Then mirror `start_interview`'s session block exactly: new `session_id = str(uuid.uuid4())`, `Session(id=session_id, business_id=business_id, status=SessionStatus.active, transcript_ref=session_id)`, `KBSink().save(session, ctx)`, `repo.add_turn(session_id, "question", _INTERVIEW_OPENER)`, return `{"business_id": business_id, "session_id": session_id, "question": _INTERVIEW_OPENER}`.
c. `POST /interviews` is untouched — assert its existing tests pass unchanged (D3).

**Acceptance criteria (new test file, mirroring `tests/test_get_business_sessions_api.py`'s seeding/helper style):**
1. 401 on missing and on garbage token.
2. Unknown `business_id` and wrong-tenant request return the identical 404 (same status AND body), and neither creates a session row or any turn (assert via direct repo read).
3. Happy path: 200 with `{business_id, session_id, question == _INTERVIEW_OPENER}`; the session exists via `GET /businesses/{id}/sessions` with `status == "active"` and non-null `started_at`; the transcript endpoint returns exactly one `question` turn.
4. Second-interview integration: business created via `POST /interviews`, driven to completion, then `POST /businesses/{id}/interviews` → answer the new session via the existing `POST /interviews/{sid}/answer` to completion → it yields its **own** recommendation; `GET /businesses?tenant=` shows `session_count == 2` for the one business (no duplicate business row); the first session's status, transcript, and recommendation are byte-for-byte untouched.
5. Answering a *completed* session still 409s ("interview already complete") — locking in D4's no-reopen rule as a stated guarantee of this feature, not just a leftover.
6. `.\run-tests.ps1` green.

### Item 3 — UI: "New Interview" action per business row on `/ui/businesses`

**Changes: `web/templates/businesses.html` + `tests/test_ui.py`:**
a. In `renderResults`'s actions cell (alongside Sessions/Rename/Delete): a "New Interview" `<button type="button">` plus its own per-row `error-message` div (matching the Sessions/Rename error pattern).
b. Click handler: clear the row error, `fetchWithAuth("/businesses/" + encodeURIComponent(business.id) + "/interviews?tenant=" + encodeURIComponent(tenant), {method: "POST"})`; on 401 → "You are not authorized to start an interview."; 404 → "Business not found."; other non-OK/network → the standard generic messages (mirror the rename handler's branch structure). On 200: write `sessionStorage.pf_interview_state = JSON.stringify({session_id: data.session_id, business_id: data.business_id, tenant: tenant, question: data.question})` — the exact shape `dashboard.html` writes — then `window.location.href = "/ui/interview"`. `interview.html` is not modified in any way (G3).
c. No `dataset.label` changes needed (the button lives in the existing Actions cell), no new CSS.

**Acceptance criteria:**
1. `tests/test_ui.py` string assertions: the "New Interview" button text, the `POST` fetch URL fragment (`/interviews?tenant=`), the `pf_interview_state` write containing `session_id`/`business_id`/`tenant`/`question`, and the `/ui/interview` navigation are all present in the rendered page.
2. All pre-existing businesses-page assertions (Sessions toggle, Rename, Resume, both delete flows, stacked-table `dataset.label`s) pass unchanged; `innerHTML` grep still zero.
3. Error branches present for 401/404/non-OK/network (assert at least the two specific messages).
4. `.\run-tests.ps1` green.

### Item 4 — Docs closeout

Verify/update `USER_MANUAL.md` (plain-language: the Businesses page now loads your last tenant automatically; each business now has a "New Interview" button for asking about another process at the same business — no jargon) and `CLAUDE.md`'s Status (new endpoint + both UI behaviors). No-op checkpoint if G6 was honored per-cycle.

**Acceptance criteria:** both docs cover the auto-load behavior, the new endpoint, and the New Interview action; full `.\run-tests.ps1` green.

**Suggested sequencing:** 1 (independent) → 2 → 3 (strictly after 2) → 4.

---

## Part D — Judgment calls the Arbiter must not silently re-decide

1. Auto-load errors are **shown** on `/ui/businesses` (unlike the dashboard's deliberate quiet swallow) — the list is the page's whole purpose (D1).
2. One shared `loadBusinesses()` path for submit and on-load — no second dashboard-style copy of the fetch logic (D1).
3. Endpoint is `POST /businesses/{business_id}/interviews` with `tenant` as a **query param** and **no body** — matching the business-scoped siblings, deliberately unlike body-tenant `POST /interviews` (D2).
4. "Ask more questions" = new clean-slate session; reopening/appending to a completed session is **rejected**, consistent with the first spec's D3 and the existing 409 gate; `refine` remains the post-hoc path (D4/A3). This spec explicitly upholds, not contradicts, that precedent.
5. `POST /interviews` untouched; no existing-business picker on the dashboard — `/ui/businesses` is the single entry point for the new action (D3).
6. Duplicate-business merge/dedup not attempted (D5, Part E).
7. No `Session` contract change, no per-session topic label (D6).
8. Fixed `_INTERVIEW_OPENER` reused verbatim — no context-aware LLM opener (D7).
9. Multiple concurrent `active` sessions per business are allowed, on purpose (D8).
10. Response shape of the new endpoint is identical to `POST /interviews`'s, specifically so `interview.html` and the `pf_interview_state` contract need zero changes.

## Part E — Explicitly OUT OF SCOPE

- Merging or deduplicating already-existing duplicate business rows (e.g. two "dogsrus" businesses) — manual Rename/Delete already exist.
- Reopening, editing, or appending turns to a completed session; any change to `answer_interview`'s 409 gate.
- Any `contracts/records.py` change (session topic/label, business links); any migration; any `kb/repository.py` change (none is needed).
- Business-context-aware opening questions (LLM in the start endpoint).
- An existing-business dropdown/picker on the dashboard start form.
- The two pre-existing bugs carried in the first spec's Part E (double-submit duplicate question turns; recommendation summary showing raw task UUIDs) — still not planned here.

## Part F — Could not verify statically / needs Brian's live check later

1. **Actual browser behavior** — `tests/test_ui.py` is string-assertion only (no JS engine): the auto-load firing on real page load, the New Interview button's full round-trip into `/ui/interview`, and error rendering all need a quick live click-through after landing. Suggested check: open `/ui/businesses` with a remembered tenant (list appears with no click), press New Interview on an existing business, answer one question, then confirm the Businesses page shows `session_count` incremented and two sessions listed.
2. **Conversation quality of a second interview** — whether the live LLM path asks sensible questions for a *second* process at a known business is the same "only a human judges conversation quality" carve-out both prior specs used.
3. **Stale `session_count` on the businesses page after returning from a completed second interview** is refreshed by the new auto-load itself (page reload refetches) — expected to just work, but it's a live-behavior claim, not a tested one.
4. Whether Brian eventually wants a per-session topic label to tell a business's sessions apart (D6 deferred it) — a product call after he's used multi-session businesses for real.
