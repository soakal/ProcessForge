# ProcessForge: Dashboard History, Business/Session Management, Longer Interviews, Operator Management — Implementation Spec

**Planned by Fable, 2026-07-18. Council-loop's Arbiter decomposes this into cycles — one item is
roughly one cycle (some items may need 2). Do not skip acceptance criteria. Do not touch anything
listed in Part E (explicitly out of scope).**

---

## Part A — Investigation findings

### A1. Confirmations of the brief (all verified true against the live code)

1. **No list/read endpoints for businesses or sessions exist.** No `GET /businesses`, no `GET /businesses/{id}`, no business edit endpoint, no session list, no session delete. `kb/repository.py` has no `list_businesses` method — `list_by_business("sessions", ...)` exists but nothing enumerates businesses for a tenant.
2. **`dashboard.html` is only the start-interview form** (business_name + tenant → `POST /interviews` → redirect to `/ui/interview` with state in `sessionStorage.pf_interview_state`).
3. **`businesses_delete.html` requires a blind-typed raw UUID**, retyped to confirm; no browse, no edit.
4. **`_MAX_INTERVIEW_ANSWERS = 6`** (`api/main.py:70`), checked at line 411 (`answer_count >= _MAX_INTERVIEW_ANSWERS` → forced `question = None` **before** `next_question()` is even called). It is a module-level constant, not env-configurable.
5. **Two stopping mechanisms confirmed:** (a) the API hard cap; (b) `interviewer.next_question()` — LLM path returns `None` when the model answers `{"complete": true}`; deterministic fallback `_next_question_deterministic(answer_count)` returns questions for `answer_count <= 5` and **`None` for `answer_count >= 6`** (`stages/interviewer.py:139-172`). If the hard cap is raised, the deterministic fallback does NOT loop or repeat — it cleanly returns `None` at 6 answers, which the API's completion path already handles. No new ladder rungs are required for correctness; only its docstring needs updating, plus a test locking in the fallback-completes-at-6 behavior.

### A2. Bug found during planning (blocks ask #4 — must be in scope, see Item 1)

**`KBRepository.delete_business` hard-fails with `sqlite3.IntegrityError: FOREIGN KEY constraint failed` on any business created through the interview flow.** The `session_turns` table (`kb/migrations/versions/7b06fdbde0a3_session_turns.py:28`) declares `session_id TEXT NOT NULL REFERENCES sessions (id)` with **no `ON DELETE CASCADE`**, `KBRepository.__init__` sets `PRAGMA foreign_keys = ON`, and `delete_business` (`kb/repository.py:200-316`) deletes `sessions` rows **without ever deleting `session_turns`**. Reproduced live against a temp DB: business + session + 2 turns → `delete_business` → `IntegrityError` (transaction rolled back; the endpoint would 500). The existing `tests/test_delete_business_repo.py` misses it because it never seeds turns. **Every business Brian will see in the new list (interview-created ones) is un-deletable today.**

### A3. Other facts the spec relies on (verified)

- **Zero `contracts/records.py` changes are needed for any of the four asks.** `Business.name` already exists (the only sensible edit target); lists read existing data; the cap is env-configurable; session "started" time can be derived from `MIN(session_turns.ts)`.
- **Tenant pattern to replicate** (`kb/repository.py:59-64` + every endpoint): repo methods take `tenant` explicitly, SQL is `WHERE ... AND tenant = ?`, wrong-tenant and unknown-id both produce identical `404 "not found"`. `session_turns` helpers (`add_turn`/`list_turns`) are deliberately NOT tenant-scoped — callers must resolve the session tenant-scoped **first**.
- **Endpoint boilerplate to replicate** (every protected endpoint): rate-limit-before-auth (`_check_rate_limit(client_host)` first), then `db_path` from `PROCESSFORGE_DB_PATH` (never client-supplied), then `_authenticate(authorization, db_path)`, then `_open_repo` with `repo.close()` in `finally`.
- **Env-var parsing convention to replicate:** `_check_rate_limit` reads the env var **per call** (test-friendly with monkeypatch), falls back to default on blank/non-integer.
- **Tenant-search UI precedent:** `audit-log.html` — tenant input + Search button + `renderResults` building a `<table>` with `createElement`/`textContent` only. `innerHTML` is verifiably absent from all templates (grep: zero matches) — a hard rule for new pages.
- **Audit-log write path reusable for business renames:** `log_approval_change` (`kb/repository.py:126-153`) takes arbitrary `record_kind/record_id/field/old_value/new_value` — nothing in it is approval-specific except the name.
- **Auth tokens are cross-tenant:** the server cannot infer "the caller's tenant" — every list endpoint must take `tenant` as an explicit query param exactly like `GET /audit-log` does, and the UI must ask for/remember the tenant.
- **Delete-business endpoint convention:** POST verb + `/delete` suffix + confirm-field-matches-path-id checked *before any DB access*; UI mirrors the check client-side before fetching.
- **Cycle granularity:** recent history (`council: cycle 9..13`) shows one endpoint OR one page-slice per cycle, tests in the same cycle.
- **Docs discipline:** `USER_MANUAL.md` must be updated in the same change as user-facing behavior; project `CLAUDE.md` Status likewise.
- `tests/conftest.py` autouse fixture strips `PROCESSFORGE_LLM_PROVIDER` before every test — do not weaken; all LLM-path tests must use a fake `ctx.complete` / monkeypatched interviewer, never a provider.

### A4. Operator-management investigation (grounds Items 17-19)

- **Password policy:** minimum 8 chars, enforced at the *caller* layer (`auth/users.py:19` `_MIN_PASSWORD_LENGTH = 8`; also imported and enforced by `desktop/setup_account.py:_validate`). PBKDF2-HMAC-SHA256, 600k iterations.
- **Uniqueness:** DB-enforced (`operators.username UNIQUE`); `IntegrityError` → `DuplicateOperatorError`.
- **Token revocation already exists and is correct:** `set_password()` deletes **all** of that operator's `auth_tokens` rows, and `delete_operator()` deletes tokens first, then the operator row.
- **`list_operators()` returns only `username` + `created_at`** — never `password_hash`. But `get_operator()` **does** return `password_hash`; new endpoints must never surface that method's dict in a response.
- **A third management surface already exists:** `desktop/setup_account.py` has `create_account()`/`update_password()`/`_validate()` (tkinter GUI, gated under `__main__`) — a lockout-recovery path independent of the web UI.
- **No role/admin concept anywhere** — grep of `auth/` for `role|admin|is_admin` returns zero matches; `operators` table is `id/username/password_hash/created_at` only. All operators are flat and equal (repo CLAUDE.md documents this as deliberate — "Brian's team are the only people who log in").
- **Tenant isolation confirmed not applicable** to auth tables — deliberately outside `kb/repository.py`'s tenant machinery.

---

## Part B — Design decisions (with justification)

**D1 — Interview cap: keep a hard cap, make it env-configurable, default 12.** New env var `PROCESSFORGE_MAX_INTERVIEW_ANSWERS`. Why not remove the cap: it is the documented cost/DoS bound on a runaway adaptive LLM loop — the LLM's own `{"complete": true}` judgment is the *intended* stop, the cap is the *safety* stop. Why 12: doubles the question budget while keeping the deterministic-fallback behavior coherent. No new ladder rungs (see A1.5).

**D2 — "Edit a business" = rename (`name` field) only.** `id` is immutable (FK anchor), `tenant` must be immutable (would desync every child row's tenant column), `meta` is machine-managed with no UI semantics. Renames are audit-logged. No `approval_state` interaction.

**D3 — "Edit an interview" = view + resume + delete, NOT retro-editing past answers.** Editing historical transcript turns would silently desync derived Tasks/Opportunities/Recommendations with no re-run semantics. `POST /recommendations/{id}/refine` is already the sanctioned way to add information after the fact.

**D4 — Navigation: one new `businesses.html` management page; list/view/edit/delete all live there.** New nav link "Businesses" (`/ui/businesses`) replaces the "Delete Business" nav link in `base.html`. Sessions are folded into the businesses page as expandable per-business rows rather than a separate page. The dashboard gets a compact read-only "Your businesses & past interviews" section.

**D5 — Sessions list shape: `GET /businesses/{id}/sessions` (business-scoped), not a global `GET /sessions`.** Matches how the data hangs together; reuses the existing `list_by_business` repo method.

**D6 — Operator management: no admin role — flat privilege stands, with a self-delete ban as the only guardrail.** Inventing an admin tier needs a migration, a bootstrap answer, and role-management UI, for a product whose CLAUDE.md explicitly says the operator population is Brian's small trusted team. Instead: any logged-in operator may list/create/reset/delete, **except their own account** (server-enforced 409) — this structurally guarantees the operator count can never reach zero through the web. **Flagged risk Brian should sign off on:** any operator can still delete or password-reset any *other* operator; recovery always exists via CLI (`python -m auth.users`) or the desktop wizard on the host.

**D7 — Username-in-body, not in-path, for operator mutations.** Usernames are unconstrained strings today (no charset rule), so path-param routes would break on `/`-containing usernames. Body-based mutations sidestep this. Deliberate deviation from the `/X/{id}/delete` pattern (safe there because those ids are server-generated UUIDs).

**D8 — No current-password check on self password change.** In a flat model where any operator can reset any *other* operator's password without knowing it, requiring the current password only on the self path adds ceremony, not security.

**D9 — Self password change = immediate sign-out, by design.** `set_password()`'s existing revoke-all-tokens behavior is kept; the UI turns that into an explicit redirect to login.

**D10 — No `audit_log` rows for user management.** The existing `audit_log` is tenant-scoped and purpose-built for approval-state changes; operators have no tenant. Accepted gap, not silently dropped — server-log-only this round (see Part F).

---

## Part C — Numbered implementation spec

**Global requirements binding every item (the Arbiter should treat violations as automatic REVISE):**
- **G1** New endpoints copy the existing boilerplate exactly: rate-limit-before-auth → `PROCESSFORGE_DB_PATH` from env → `_authenticate()` → `_open_repo()`/`AuthRepository` with `close()` in `finally`.
- **G2** Tenant isolation (where applicable — auth/operator endpoints are exempt per A4): every new repo read/write is `WHERE ... AND tenant = ?`; wrong tenant ≡ unknown id ≡ identical `404 "not found"`.
- **G3** No changes to `contracts/records.py`. No new pip dependencies. No changes to `stages/builder.py`'s approval gate or any approval-state write path outside the existing approve endpoint.
- **G4** Templates: extend `base.html`, `requireAuth()` first line of the script block, all API calls via `fetchWithAuth`, all dynamic DOM via `createElement`/`textContent` — zero `innerHTML` anywhere in `web/templates` (grep must stay at zero matches).
- **G5** Every cycle lands its tests in the same commit, following existing seeding/fixture styles. `.\run-tests.ps1` green per cycle.
- **G6** `USER_MANUAL.md` and `CLAUDE.md` Status updated in the same change as any cycle that alters user-facing behavior.
- **G7** Passwords never echoed in any response body, log line, or error detail (including Pydantic `422` validation errors — validate password fields inside the handler, not via a `field_validator`, so the raw value never rides in an error response).

### Item 1 — Fix the `delete_business` FK failure on interview businesses (blocks all delete work)
Change `KBRepository.delete_business`: inside the existing transaction, before `DELETE FROM sessions`, execute `DELETE FROM session_turns WHERE session_id IN (...)` for the gathered `session_ids`; add `"session_turns": <count>` to the returned counts dict. No migration change (explicit child-first delete matches the method's existing style).
**Acceptance:** new regression test seeding a business → session → ≥2 turns, asserting `delete_business` succeeds, returns `session_turns: 2`, `list_turns` empty after. MUST fail with `IntegrityError` pre-fix (red-then-green). Existing no-turns tests pass unchanged (`session_turns: 0`). `businesses_delete.html` needs no change. Atomicity preserved on forced mid-cascade failure.

### Item 2 — Interview cap: `PROCESSFORGE_MAX_INTERVIEW_ANSWERS`, default 12
Replace `_MAX_INTERVIEW_ANSWERS = 6` with `_DEFAULT_MAX_INTERVIEW_ANSWERS = 12` + a `_max_interview_answers()` helper reading the env var per call (defensive parse: blank/non-int/`<1` → default). Update the stale docstring comment. Update `_next_question_deterministic`'s docstring in `stages/interviewer.py` to describe the new env-var cap and state the fallback-completes-at-6 behavior is intended, independent of the cap. Add the env var to `.env.example`.
**Acceptance:** monkeypatched-always-questioning interview completes at exactly 12 (default). Env `=8` completes at 8; `=""`/`"garbage"`/`"0"` → default 12. Real fallback-ladder path (no provider) completes at 6 even with cap=12 (locks in "raised cap doesn't break the fallback"). Existing 6-answer tests updated only where they asserted the old default; `refine`'s independence from the cap untouched.

### Item 3 — Repo: `list_businesses(tenant)`
New `KBRepository.list_businesses(tenant: str) -> list[dict]`: tenant-scoped, returns business dicts + `session_count` int per row (single query), ordered by name then id.
**Acceptance:** two tenants seeded → each sees only its own rows; `session_count` correct for 0/1/n sessions; empty tenant → `[]`; returned shape asserted.

### Item 4 — API: `GET /businesses`
`GET /businesses?tenant=<t>`, G1 boilerplate, returns `list[BusinessOut]` = `{id, name, session_count}` (never `meta`). Empty list for unknown tenant (same posture as `GET /audit-log`).
**Acceptance:** 401 missing/garbage token; tenant A sees only A's businesses; unknown tenant → `200 []`; `session_count` reflects a real interview session.

### Item 5 — Repo: `list_recommendations_by_session` + `get_first_turn_ts`
(a) `list_recommendations_by_session(session_id, tenant)` — tasks (tenant-scoped) → opportunities via `json_each(o.task_ids)` (same pattern as `delete_business`) → recommendations by `opportunity_id IN (...) AND tenant = ?`. (b) `get_first_turn_ts(session_id)` — `SELECT MIN(ts) FROM session_turns WHERE session_id = ?` (not tenant-scoped, same as `list_turns` — caller resolves session tenant-scoped first).
**Acceptance:** full interview-created chain returns its recommendation(s); no-tasks session → `[]`; cross-tenant → `[]`; `get_first_turn_ts` returns opener's ts, `None` for turn-less session.

### Item 6 — API: `GET /businesses/{business_id}/sessions`
Resolve business tenant-scoped first → identical 404; then `list_by_business("sessions", ...)`; attach `started_at` (item 5b, nullable) and `recommendation_ids` (item 5a). `SessionOut = {id, status, started_at: str|None, recommendation_ids: list[str]}`.
**Acceptance:** 401 unauthenticated; identical 404 unknown-id/wrong-tenant (same status AND body); completed business → session with `status="complete"`, non-null `started_at`, real recommendation id; active session → `status="active"`, `recommendation_ids==[]`; zero-session business → `200 []`.

### Item 7 — API: `POST /businesses/{business_id}/edit` (rename)
Body `EditBusinessRequest {name: str}`, `min_length=1, max_length=500`, reject whitespace-only (strip, store stripped). G1 boilerplate; tenant-scoped fetch → identical 404; no-op rename (same name) → 200, no audit entry; real rename → `repo.put` then `repo.log_approval_change(record_kind="business", field="name", old_value, new_value)`. POST-with-suffix over PATCH for consistency.
**Acceptance:** 401; identical 404; rename persists; audit entry visible via `GET /audit-log`; whitespace-only/>500-char → 422; same-name → 200, no new audit row; rename never touches any `approval_state`.

### Item 8 — Repo: `delete_session(session_id, tenant)`
Mirror `delete_business`'s structure exactly: verify tenant-scoped existence; gather task/workflow_graph ids, intersecting opportunities, their recommendations/automations, this session's `session_turns`; delete children-first (including turns); leave `audit_log` untouched; parent business NOT deleted; return counts dict.
**Acceptance:** full chain deleted with correct counts, business survives; unknown id/wrong tenant → `None`; second call → `None`; sibling session untouched; forced mid-delete failure rolls back everything.

### Item 9 — API: `POST /sessions/{session_id}/delete`
Body `{confirm_session_id}`, exact-match check BEFORE opening any repo (mirror `delete_business`'s ordering) → 400 on mismatch; G1 boilerplate; identical 404 on unknown/wrong-tenant.
**Acceptance:** 401; mismatch → 400, DB untouched; identical 404; happy path returns counts, session disappears from the sessions list, business remains; deleting an approved-and-built recommendation's session removes those rows, writes nothing to `audit_log`, never flips any surviving `approval_state`.

### Item 10 — UI: `businesses.html` list page + route + nav
New `GET /ui/businesses` route + `web/templates/businesses.html`: tenant input + Load button (audit-log.html pattern); tenant prefilled/persisted via `localStorage.pf_last_tenant`; renders name/short-id/session-count per business via `createElement`/`textContent`. `base.html`: replace "Delete Business" nav link with `<a href="/ui/businesses">Businesses</a>` (delete route/page stays alive, reached via per-row Delete deep-links per Item 13).
**Acceptance:** page renders 200 with tenant input/Load button/`.page-intro`/`.next-step`; `requireAuth`/`fetchWithAuth`/`pf_last_tenant` presence asserted; nav shows new link, not old one; `innerHTML` grep stays zero.

### Item 11 — UI: per-business sessions expansion
Each row gets a "Sessions" toggle fetching `GET /businesses/{id}/sessions` on first expand: status, `started_at`, "Transcript" link (`/ui/interview/{sid}/transcript?tenant=`), "View recommendation" link(s). Empty → "No interviews yet" text node. Error handling mirrors `audit-log.html`.
**Acceptance:** script-presence for fetch URL, link construction, empty-state text; `innerHTML` zero.

### Item 12 — UI: inline rename on `businesses.html`
Per-business "Rename": inline input prefilled with current name + Save/Cancel; Save → `POST /businesses/{id}/edit?tenant=`; on 200 update row in place; errors per-row. No confirm-retype (non-destructive, audit-logged).
**Acceptance:** script-presence for edit URL + error branch; `innerHTML` zero.

### Item 13 — UI: delete actions
(a) Per-session inline "Delete": confirm input (type session ID) + strict `!==` client guard before fetch → `POST /sessions/{sid}/delete?tenant=`; success removes row + shows counts. (b) Per-business "Delete": link to `/ui/businesses/delete?business_id=&tenant=` (encoded). (c) `businesses_delete.html`: on load, prefill `business_id`/`tenant` from `URLSearchParams` via `.value` — **never** prefill `confirm_business_id`.
**Acceptance:** script-presence for session-confirm guard + URL pattern + the URLSearchParams prefill provably skipping `confirm_business_id`; existing delete-page tests pass unchanged; `innerHTML` zero.

### Item 14 — UI: resume an active interview
Session rows with `status==="active"` get a "Resume" action: fetch `GET /interviews/{sid}/transcript?tenant=`, find last `role==="question"` turn, write `sessionStorage.pf_interview_state = {session_id, tenant, question}` (matches `interview.html`'s guard — `business_id` optional), navigate to `/ui/interview`. No question turn found → per-row error, no navigation. Closes a real gap: today a closed tab permanently orphans an in-progress interview.
**Acceptance:** script-presence for transcript fetch + last-question selection + state write; API-level test asserting an interrupted interview's transcript ends with a question turn while active.

### Item 15 — UI: dashboard "past interviews" section
Below the start form: if `pf_last_tenant` exists, auto-fetch `GET /businesses?tenant=` and render name/session-count rows linking to `/ui/businesses`; always show a "Manage businesses" link. No tenant remembered → plain hint text. Starting an interview stores `pf_last_tenant` if not already landed by Item 10. Errors render quietly, never block the start form.
**Acceptance:** dashboard assertions for new section markup + script presence; existing dashboard tests unchanged; `innerHTML` zero.

### Item 16 — Docs closeout sweep
Verify `USER_MANUAL.md` and `CLAUDE.md` Status cover everything in items 1-15 and 17-19; add `PROCESSFORGE_MAX_INTERVIEW_ANSWERS` to CLAUDE.md's env-var section. No-op checkpoint if G6 was honored per-cycle.
**Acceptance:** both docs mention every new page/endpoint/env var; `USER_MANUAL.md` has no unexplained jargon; full `.\run-tests.ps1` green.

### Item 17 — Backend: operator-management endpoints
Four endpoints under `/auth/` (matching where login/logout live), G1 boilerplate, mutations take username in the **JSON body** (not path — see D7):
1. `GET /auth/operators` → `[{username, created_at}, ...]` from `repo.list_operators()`. Never include `password_hash`.
2. `POST /auth/operators` — `{username, password}`. Strip/reject-empty username; password `< _MIN_PASSWORD_LENGTH` (imported from `auth.users`, single source of truth) → `400`. `DuplicateOperatorError` → `409`. Success → `{"username":..., "status":"created"}`.
3. `POST /auth/operators/password` — `{username, new_password}`. Same validation. `OperatorNotFoundError` → `404`. Calls `repo.set_password()` (already revokes all that operator's tokens). No current-password field (D8).
4. `POST /auth/operators/delete` — `{username}`. `OperatorNotFoundError` → `404`. **Self-delete forbidden:** target username `===` authenticated operator's username → `409` "cannot delete your own account", checked BEFORE any DB write. Otherwise `repo.delete_operator()`.

**G7 applies here specifically:** password validation happens inside the handler (`HTTPException(400,...)` with a fixed message), never via a Pydantic `field_validator` on the password field (422 bodies echo `input`).
**Acceptance:** all four 401 identically on bad/missing token; create → appears in list; duplicate → 409, list unchanged; short password on create/change → 400, response body does NOT contain the submitted password; password change invalidates the target's prior tokens (issued-before-change token 401s after); self password change: the very token used to make the change 401s on the NEXT request (change request itself is 200 — assert both); delete: target's token 401s after, target gone from list; self-delete → 409, still listed, own token still works; sole-remaining-operator cannot self-delete (proves the system can never reach zero operators via the web); `GET /auth/operators` response contains no `password_hash` key.

### Item 18 — UI: `/ui/operators` page
New route + `web/templates/operators.html`: same conventions as every other page (`requireAuth`, `fetchWithAuth`, `createElement`/`textContent`, `.page-intro`/`.next-step`). Operator table (username, created_at) from `GET /auth/operators`, sorted by username. "Add operator" form: username + password + confirm-password, strict client-side match check before any fetch, clear both fields after any submit. Per-row "Reset password" (new+confirm, same client check) and "Delete" (retype-username confirm, strict `!==` before fetch). **Own row: no Delete control rendered** (server enforces the 409 regardless — UX only). **Self password-change:** on success, immediately clear `pf_token`/`pf_username` and redirect to `/ui/login` (server already revoked the token; a pre-submit note warns "changing your own password signs you out"). 401/network errors match existing page fallback pattern.
**Acceptance:** route renders with `.page-intro`/`.next-step`; `requireAuth`/`fetchWithAuth` referenced; zero `innerHTML`; confirm-mismatch guard precedes fetch; own-row delete-suppression present; self-change logout/redirect code present.

### Item 19 — Nav link for Operators
Add `<a href="/ui/operators">Operators</a>` to `base.html`'s nav (before `.spacer`). Fold into Item 10's cycle if that hasn't shipped yet (one `base.html` touch, not two); otherwise ride along with Item 18's cycle.
**Acceptance:** link present in `base.html`; nav test extended the same way Item 10's was.

**Suggested sequencing:** 1 → 2 (independent, small, high-value) → 3-9 (backend, each independently testable) → 10-15 (UI, strictly after their endpoints) → 16 → 17 → 18-19. Items 3/5/8 (repo) can run before their API twins in adjacent cycles; do not merge repo+API+UI into one cycle.

---

## Part D — Judgment calls the Arbiter must not silently re-decide

1. "Edit business" = rename only (D2). More editable fields is a contract conversation, not this spec.
2. "Edit interview" = view/resume/delete, never retro-editing answers (D3). `refine` is the sanctioned post-hoc path.
3. Cap kept (not removed), env-configurable, default 12; no new ladder rungs (D1).
4. `started_at` derived from `MIN(session_turns.ts)`, nullable — zero contract/migration churn.
5. Business delete stays on the dedicated confirm page (deep-linked + prefilled, confirm field never prefilled); only session delete gets an inline confirm.
6. Tenant selection is manual/remembered (`pf_last_tenant`) — tokens are cross-tenant, server can't infer it.
7. `GET /businesses` returns `200 []` for unknown tenant, not 404 — a list has no id to protect.
8. Rename reuses `log_approval_change` despite its name — generic columns, zero migration; a stale method name was judged better than touching reviewed code.
9. No admin role — flat privilege + self-delete ban (D6). Any operator can still affect any *other* operator — explicitly flagged risk, not an oversight.
10. Username-in-body for operator mutations, not path params (D7).
11. No current-password check on self password change (D8).
12. Self password change = immediate sign-out by design (D9).
13. No `audit_log` rows for operator management this round (D10) — accepted gap, see Part F.

## Part E — Explicitly OUT OF SCOPE (do not let cycles drift into these)

- **Pre-existing bug (a):** double-submitted answer (~2s apart) produced two consecutive question turns with no answer between. Not planned here — Brian decides separately. (Item 14's resume logic tolerates it by construction — takes the *last* question turn.)
- **Pre-existing bug (b):** recommendation `summary` renders a raw task-UUID list instead of the task description. Not planned here.
- Editing historical transcript turns; editing `Business.meta` or `tenant`; any `approval_state` write path beyond the existing approve endpoint; global `GET /sessions`; pagination; per-tenant auth; any `contracts/records.py` change; any new dependency or frontend framework.
- An admin/role system for operators — see D6.
- Audit trail for operator management actions — see Part F.
- Converting `session_turns`'s FK to `ON DELETE CASCADE` (SQLite table-rebuild churn for no behavioral gain over the explicit delete in Item 1).

## Part F — Could not verify statically / needs Brian's live check later

- **Real-LLM interview length past 6 answers:** all tests mock `ctx.complete`. Whether OpenRouter's live judgment asks useful questions 7-12 is a human/live check after Item 2 lands — same "only a human judges conversation quality" carve-out already established for Loop 2.
- **`started_at` for legacy rows:** existing one-shot sessions in the live DB will show blank dates — expected.
- **Audit trail for operator changes:** whether Brian wants a durable "who deleted/reset whom" record is a product call, not decided here — currently server-log-only (D10).
- **Desktop wizard drift:** `desktop/setup_account.py`'s password policy is single-sourced today; not verified whether the packaged `ProcessForgeSetup.exe` bundles a stale copy (build artifacts in `dist/` not inspected).
- The `dogsrus` live-test data from the interview test session presumably still exists in the dev DB — untouched by this planning process (FK-bug reproduction used a throwaway temp DB only).
