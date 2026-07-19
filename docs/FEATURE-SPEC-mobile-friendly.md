# ProcessForge: Mobile-Friendliness Pass — Implementation Spec

**Planned by Fable, 2026-07-18.** Target repo: `C:\Users\Brian\Documents\ProcessForge`. Scope: `web/templates/*.html`, `web/static/app.css`, and `tests/test_ui.py` only. Council-loop's Arbiter decomposes into cycles — one item is roughly one cycle.

---

## Part A — Investigation findings (verified against actual code)

### A1. Confirmed current state

- `web/templates/base.html:5` has the viewport meta. So does `login.html:5` (login does NOT extend base — it duplicates its own full `<head>`; any purely-CSS fix reaches it automatically, but any base.html markup change must be checked against login separately).
- `web/static/app.css` (152 lines, read in full): **zero `@media` queries**.
- Nav (`base.html:10-17`): flat flexbox, `gap: 1.25rem`, **no `flex-wrap`** — flex default is `nowrap`, so at 375px the 5 items (Dashboard / Audit Log / Businesses / Operators / spacer / Log Out) will shrink-squash and/or overflow, not wrap. Nav links have no padding of their own — tap target height is well under 44px.
- `main { max-width: 960px; padding: 1.5rem }` — 48px of a 375px viewport gone to padding.
- No frontend framework, no build step, Jinja2 + vanilla JS. Confirmed via templates + `requirements.lock.txt`.

### A2. Test tooling — confirmed string-assertions only

`tests/test_ui.py` (353 lines) uses `fastapi.testclient.TestClient` and asserts on `response.text` substrings — no playwright/selenium/puppeteer in `requirements.lock.txt`. So: `@media` presence, selector presence, and markup/JS-fragment presence are testable; actual reflow, wrap behavior, and touch comfort are not. This spec carries its own Part F for live-check items, matching `docs/FEATURE-SPEC-dashboard-and-users.md`'s precedent.

### A3. Full friction inventory, per file

**`app.css` (global):**
1. No media queries at all (the headline gap).
2. `.nav` — no wrap, no touch-sized link targets.
3. `main` padding 1.5rem — too fat at 375px.
4. Text/password inputs are styled at `font-size: 1rem` (16px — good, prevents iOS Safari's auto-zoom-on-focus), **but `textarea` has no rule at all** — the feedback textarea on recommendations.html gets browser-default ~13px font (tiny + triggers iOS focus-zoom), no width rule either.
5. Buttons: `padding: 0.55rem 0.9rem` at 1rem font ≈ 40px tall — just under the 44px touch-target guideline. JS-created buttons (Sessions/Rename/Resume/Delete/Save/Cancel — up to 8 per business row) inherit this same rule.
6. `table { width: 100% }` with no overflow container anywhere — any table wider than the viewport widens the whole page (the classic broken-mobile-page signature).

**`audit-log.html`:** JS builds a **7-column table** (Timestamp, Operator, Record Kind, Record ID, Field, Old Value, New Value) where two columns are full UUIDs and one is an ISO timestamp. Guaranteed massive horizontal overflow at 375px. Read-mostly, zero interactive controls inside it.

**`businesses.html`:** 4-column table (Name / ID / Sessions / Actions) where the Actions cell contains a Sessions toggle, expandable sessions div (transcript link, recommendation links, optional Resume, delete-confirm input+button), a Rename toggle+input+Save/Cancel, and a Delete deep-link. Interactive-heavy → needs stacking, not sideways scrolling. ID column already shortened to 8 chars.

**`operators.html`:** same table shape, 3 columns (Username / Created / Actions), Actions holds Reset Password (2 password inputs + Save/Cancel) and Delete (confirm input + 2 buttons). Same stacking need as businesses.

**`recommendations.html`:** `<pre id="automation-spec">` gets `JSON.stringify(automation.spec, null, 2)` — never wraps, forces horizontal page overflow once an automation is built. The feedback `<textarea>` hits the unstyled-textarea gap above.

**`interview.html`** (most likely used live on a phone, mid-client-conversation): already narrow — one question `<p>`, one 320px-max form, one 16px-font input, one submit button. No tables, no overflow risk. Needs only the global fixes (main padding, button touch size) — deliberately no dedicated item.

**`transcript.html`:** stacked divs, `textContent` only — inherently mobile-safe. No CSS rule for `.transcript-turn` (no visual separation), a nice-to-have not a blocker.

**`dashboard.html`, `login.html`, `businesses_delete.html`:** forms capped at 320px + lists — safe once globals land.

### A4. Decisions (with reasoning)

**D1 — Nav: CSS-only `flex-wrap: wrap`, no hamburger.** A hamburger needs open/closed JS state, aria-expanded toggle, focus handling — a new interactive pattern in a codebase whose entire JS surface is fetch-render-guard, just to hide 4 short links. `flex-wrap: wrap` + per-link padding makes the nav two comfortable rows at 375px, byte-identical on desktop. Zero JS, zero state. Revisit only if the nav grows past ~7 items.

**D2 — One breakpoint: `@media (max-width: 640px)`.** `main` is already fluid under 960px, so 641-959px (tablet) needs nothing; the only width-dependent behaviors are card-stacking two tables and touch/padding adjustments — one cutoff serves both. 640px (not 768px) because the interactive tables still work fine at ~700px — stacking should only kick in where genuinely needed. Every `@media` in this pass MUST use exactly `(max-width: 640px)` — this establishes the repo's breakpoint convention (there is none today).

**D3 — Table treatments, per table (deliberately not uniform):**
- **audit-log** (7 cols, read-only, dense forensic data): horizontal-scroll wrapper. Comparing old/new values across columns is the point of this table; stacking 7 labeled fields per entry would make one entry fill two phone screens. Costs one CSS rule on the existing `#audit-log-results` container — no markup or JS change.
- **businesses + operators** (few cols, interactive controls per row): CSS card-stacking at ≤640px (`display: block` on table parts, `thead` hidden, `td::before { content: attr(data-label) }`). Controls and confirm inputs get full width instead of a squeezed/panning cell. Needs a one-line JS addition per `<td>` (set `dataset.label`) — attribute-only, keeps `createElement`/`textContent` discipline intact.

**D4 — Touch sizing scoped to the ≤640px query** (`min-height: 44px` on buttons, nav-link padding globally since it's harmless on desktop). Global 44px buttons would subtly change every desktop screenshot for no reason.

---

## Part B — Global constraints (Arbiter: enforce on every item)

1. **Frontend-only.** No item may touch any backend endpoint, any Python file, `api/main.py`, any `contracts/`/`stages/`/`kb/`/`auth/` file, or any test file other than `tests/test_ui.py`. Allowed files: `web/static/app.css`, `web/templates/*.html`, `tests/test_ui.py`. `web/static/app.js` needs no change in any item; leave it alone.
2. **No framework, no build step, no new dependency** — no Bootstrap/Tailwind/CDN links, no npm anything.
3. **XSS discipline unchanged:** zero `innerHTML` (existing tests assert its absence — keep them passing), all new DOM attributes set via `dataset`/`setAttribute` on `createElement` nodes.
4. **Do not rename/remove any element id, class, or JS-depended-on structure.** `tests/test_ui.py` string-asserts dozens of exact fragments — run `.\run-tests.ps1` before claiming any item done.
5. **Breakpoint is exactly `@media (max-width: 640px)`** everywhere. No second breakpoint may be introduced.
6. **Desktop must be visually unchanged** above 640px except where an item explicitly says otherwise (nav link padding, textarea styling).
7. `USER_MANUAL.md` needs no update unless an item changes what a user must *do* (none of these do — behavior is identical, layout adapts).

---

## Part C — Numbered implementation spec

### Item 1 — Global responsive foundation in `app.css` (nav wrap, spacing, touch targets, textarea)

Changes, all in `web/static/app.css`:
a. `.nav` gains `flex-wrap: wrap;`, `gap` becomes `0.5rem 1.25rem`. `.nav a, .nav button.link` gain `padding: 0.5rem 0;`.
b. New `textarea` styling: same padding/border/border-radius as inputs, `font-size: 1rem` (16px, prevents iOS focus-zoom), `width: 100%; max-width: 320px;`.
c. New `@media (max-width: 640px)` block: `main { padding: 1rem 0.75rem; }`; `button { min-height: 44px; }`; `input[type="text"], input[type="password"], textarea { min-height: 44px; }`.
d. A short comment above the media query noting it's the repo's single breakpoint convention.

**Acceptance criteria:**
1. `GET /ui/static/app.css` body contains `(max-width: 640px)` and no other `max-width:` value anywhere in an `@media` rule.
2. CSS contains `flex-wrap: wrap` within `.nav` and `min-height: 44px`.
3. CSS contains a `textarea` selector with `font-size: 1rem`.
4. New/extended `tests/test_ui.py` assertions cover 1-3 via the `/ui/static/app.css` route.
5. Full `.\run-tests.ps1` green.

### Item 2 — Viewport-meta lock-in for every page (test-only item)

No product code should need changing — `base.html:5`/`login.html:5` already carry the viewport meta — but nothing asserts it, so this pass's foundational assumption is one refactor away from silently vanishing.

**Changes:** `tests/test_ui.py` only — extend each existing per-page render test (login, dashboard, interview, recommendation, transcript, audit-log, businesses, businesses-delete, operators) with an assertion for the exact viewport meta string.

**Acceptance criteria:**
1. All 9 page-render tests assert the exact viewport meta string.
2. No file other than `tests/test_ui.py` touched.
3. `.\run-tests.ps1` green.
(If the Engineer finds a page whose rendered output actually lacks the meta, fix it in the template — per current reading, all 9 pages inherit it.)

### Item 3 — Audit-log table: horizontal-scroll wrapper

**Changes:**
a. `web/static/app.css`: `#audit-log-results { overflow-x: auto; -webkit-overflow-scrolling: touch; }`, `#audit-log-results table { min-width: 640px; }`, `#audit-log-results th, #audit-log-results td { white-space: nowrap; }`.
b. No markup or JS change — the results div already exists.

**Acceptance criteria:**
1. CSS contains `#audit-log-results` with `overflow-x: auto`; new test asserts presence.
2. `audit-log.html` itself has zero diff (CSS + tests only).
3. Live check (Part F): audit table pans horizontally inside its container while the page body itself does not scroll sideways.
4. `.\run-tests.ps1` green.

### Item 4 — Businesses table: card-stacked rows at ≤640px

**Changes:**
a. `web/templates/businesses.html` script: where the four `<td>`s are created, set `nameCell.dataset.label = "Name"`, `idCell.dataset.label = "ID"`, `countCell.dataset.label = "Sessions"`, `actionsCell.dataset.label = "Actions"`. Attribute-only.
b. `web/static/app.css`, inside the `@media (max-width: 640px)` block: set `table.className = "stacked"` in the same JS spot, then CSS rules: `table.stacked, table.stacked tbody, table.stacked tr, table.stacked td { display: block; width: 100%; }`, `table.stacked thead { display: none; }`, `table.stacked td { border-bottom: none; }`, `table.stacked tr { border-bottom: 1px solid #ddd; padding: 0.5rem 0; }`, `table.stacked td::before { content: attr(data-label); display: block; font-weight: 600; font-size: 0.8rem; color: #555; }`, `table.stacked td input[type="text"] { width: 100%; }`.

**Acceptance criteria:**
1. Businesses page text contains all four `dataset.label` assignments and `table.className = "stacked"`; CSS contains `table.stacked` and `content: attr(data-label)` inside the 640px media block; `innerHTML` still absent; every pre-existing businesses assertion still passes.
2. No change to any fetch URL, guard, or handler — diff is attribute/class assignments only.
3. Live check (Part F): at 375px each business renders as a labeled stacked card; Sessions expansion, Rename box, session-delete confirm input, Resume all usable without horizontal scrolling; desktop (>640px) unchanged.
4. `.\run-tests.ps1` green.

### Item 5 — Operators table: same card-stacking treatment

**Changes:**
a. `web/templates/operators.html` script: `table.className = "stacked"`; `usernameCell.dataset.label = "Username"`, `createdCell.dataset.label = "Created"`, `actionsCell.dataset.label = "Actions"`.
b. CSS: reuse Item 4's shared `table.stacked` rules (do not duplicate). Extend the width rule to `table.stacked td input[type="text"], table.stacked td input[type="password"] { width: 100%; }` (add the password selector here if not already added in Item 4).

**Acceptance criteria:**
1. Operators page text contains the three `dataset.label` assignments and `table.className = "stacked"`; all existing operators-page assertions (including guard-ordering checks) still pass; `innerHTML` still absent.
2. CSS contains the `input[type="password"]` width selector within the stacked rules; still only 640px media queries repo-wide.
3. Live check (Part F): at 375px, Reset Password's two fields + Save/Cancel and Delete's confirm flow are full-width and tappable; own-row Delete suppression unaffected.
4. `.\run-tests.ps1` green.

### Item 6 — Recommendations page: spec `<pre>` overflow + remaining-page sweep

**Changes:**
a. `web/static/app.css`: `#automation-spec { overflow-x: auto; max-width: 100%; }` (scroll, not `pre-wrap` — wrapping would destroy the JSON indentation structure).
b. `web/static/app.css`: `.transcript-turn { margin-bottom: 0.75rem; }` and `.transcript-turn p { margin: 0.15rem 0; }`.
c. Sweep verification (expected zero-diff): confirm `interview.html`, `dashboard.html`, `login.html`, `businesses_delete.html`, `transcript.html` contain no fixed-width element, table, or `pre` outside what Items 1-6 already cover. If a genuine friction point is found, fix it within this item and add a matching test assertion.
d. `tests/test_ui.py`: assert `#automation-spec` CSS rule presence with `overflow-x: auto`, and `.transcript-turn` rule presence.

**Acceptance criteria:**
1. Both new CSS rules present and asserted; no template diffs unless (c) found something, each with its own assertion.
2. Repo-wide, `app.css` still contains only `(max-width: 640px)` media queries.
3. Live check (Part F): after building an automation on a phone, the JSON spec pans inside its own box; page body never scrolls horizontally; feedback textarea is 16px-font, no iOS focus-zoom.
4. `.\run-tests.ps1` green.

---

## Part F — Could not verify statically / needs Brian's live phone check later

The test suite (FastAPI TestClient string assertions, no browser engine) cannot render CSS or execute JS. After all 6 items land, ideally on a real iPhone/Android at ~375-414px, check:

1. **Nav wraps to two clean rows** at phone width; Log Out reachable; nothing clipped.
2. **No page ever scrolls horizontally at the body level** — the definitive pass/fail for this whole effort. Audit-log table and automation-spec `<pre>` pan inside their own containers only.
3. **Businesses/operators stacked cards**: every control (Sessions expand, Rename, Resume, both delete confirm flows, Reset Password) is comfortably tappable and full-width; no overlap.
4. **No iOS focus-zoom** on any input or the feedback textarea (all should be 16px).
5. **interview.html live on a phone mid-conversation** — the priority page: question readable, answer input + Submit comfortably usable one-handed. Required no dedicated changes (verified already-narrow); this is pure confirmation.
6. **Desktop regression eyeball**: all 9 pages above 640px look identical to before the pass (except deliberately: slightly taller nav links, styled textarea).
