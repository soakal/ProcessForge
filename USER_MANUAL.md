# ProcessForge — User Manual

This manual is for anyone using ProcessForge, not just programmers. It explains what
the system does today, how to set it up, and how to use it, in plain language.

If you're technical and want the full engineering detail (contracts, database schema,
internal architecture, security model), see `CLAUDE.md` in this same folder instead.

---

## What ProcessForge does today

ProcessForge helps you figure out which repetitive business tasks are worth automating.

You tell it about a task you do (by typing some text describing it), and it:

1. Figures out what the task is and what you want the end result to be.
2. Tries to guess how long the task takes and how often you do it, by looking for
   phrases like "2 hours" or "30 min", and words like "daily" or "monthly" in what
   you typed.
3. Estimates how many hours you could save by automating it (given as a
   range, not a single exact number).
4. Writes a short draft recommendation and leaves it waiting for a person to approve.

Nothing is ever built or run automatically. A human always has to approve a
recommendation before anything is created.

**There are now two ways to describe a task:**

1. **All at once** (`/sessions`, unchanged since earlier) — you type everything in
   one go and get one result back immediately.
2. **A real back-and-forth conversation** (`/interviews`, new) — ProcessForge asks
   you one question at a time, you answer, it asks a follow-up (or decides it has
   enough and wraps up), same as talking to a person doing an intake interview. See
   "Having a real conversation" below for how to use this.

Both ways end up extracting the same kind of task/estimate/recommendation — the
conversational version just gets there by asking rather than requiring you to guess
what to include in one big answer up front.

**If you've connected an AI service** (see Setup, step 2), ProcessForge uses it to
read what you typed and figure out the task/timing/frequency details (for
`/sessions`) or decide what to ask next (for `/interviews`) — a step up from simple
phrase-matching, since it can understand context rather than just spotting exact
words like "daily." **If you haven't connected an AI service, or the AI call fails
for any reason** (no connection configured, a network hiccup, or the AI's answer
doesn't make sense), ProcessForge automatically falls back to a predictable,
rule-based approach instead: for `/sessions`, it looks at the first line of what you
typed as the task description, the last line as the outcome you want, and scans
everything in between for time/frequency clues; for `/interviews`, it asks the same
fixed 6 questions every time (how long/how often, then desired outcome, then where
the input files or source data live, then any filter rules or specific column
values that matter, then the desired output format, then it's done). You always get
a usable result either way — the fallback exists specifically so a flaky AI
connection never breaks things, for either kind of session.

---

## Setup

You only need to do this once per computer (or once per server, if someone else is
hosting this for you).

### 1. Create an isolated Python environment and install what's needed

A "virtual environment" is just a private folder that keeps this project's Python
tools separate from anything else on your computer, so nothing conflicts.

From inside the ProcessForge folder, run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

The first line creates the private folder (called `.venv`). The second line installs
everything ProcessForge needs into it, using the exact versions that have been
tested together.

### 2. Set up your configuration file

ProcessForge reads its settings from a file named `.env`. A copy of that file with
blank values, called `.env.example`, is already included. Copy it and fill it in:

```powershell
Copy-Item .env.example .env
```

Then open `.env` in a text editor and fill in these values. The first one is the
only one you truly need right now; the second is optional but worth knowing about.
(You do NOT set a password in `.env` anymore — see step 4 below for how login
actually works now.)

- **`PROCESSFORGE_DB_PATH`** — this is where ProcessForge stores its data (the tasks,
  estimates, and recommendations it creates) on your computer, as a file. The
  example default (`./kb/processforge.db`) is fine to start with.
- **`PROCESSFORGE_RATE_LIMIT_PER_MINUTE`** — this limits how many requests any one
  visitor can send in a single minute, so one person (or a runaway script, or
  someone abusing the system) can't overload ProcessForge. You can leave this blank
  to use the built-in default (30 requests per minute per visitor), or set your own
  number. If someone goes over the limit, they get an error response that says
  "Rate limit exceeded" — that just means slow down: wait a minute and send the
  request again.

Everything else in `.env` (the lines starting with `PROCESSFORGE_LLM_`,
`PROCESSFORGE_MODEL_`, and `PROCESSFORGE_OLLAMA_HOST`) is about connecting an AI
service. This is optional — you can leave those blank and ProcessForge still works,
using its predictable rule-based fallback for every session (see the honesty check
above). If you do want the smarter version, see `PROCESSFORGE_LLM_PROVIDER` in
`.env.example` for the three supported options and `python -m llm.secrets set
<provider>` for storing the key securely (never put a real key directly in `.env`).
`BUILD_LOG_URL` and `BUILD_LOG_TOKEN` are only used by the developers building
ProcessForge itself; you can leave those blank too.

### 3. Check that everything is set up correctly

Before you start using ProcessForge, run this quick check. It confirms that
everything installed correctly and that nothing is broken:

```powershell
.\run-tests.ps1
```

If this finishes without errors, your setup is good. If it reports a problem, fix
that before continuing — don't try to use ProcessForge with a failing check.

### 4. Create your own login

Nobody shares one password anymore — each person who uses ProcessForge has their
own account. Create yours (only needs to be done once per person):

```powershell
.\.venv\Scripts\python.exe -m auth.users create YOUR_USERNAME
```

It will ask you to type a password (at least 8 characters) — what you type won't
show on screen, that's normal for password prompts. Your password is stored
securely (hashed, never as plain readable text). To see everyone who has an
account, run `.\.venv\Scripts\python.exe -m auth.users list`. To remove someone's
account, `.\.venv\Scripts\python.exe -m auth.users delete THEIR_USERNAME`.

To **change a password** later (yours or someone else's — for a forgotten
password, or just to rotate it), run:

```powershell
.\.venv\Scripts\python.exe -m auth.users passwd YOUR_USERNAME
```

It asks for the new password the same way. Changing a password automatically
signs that account out everywhere it was logged in, so anyone using the old
password will have to log in again with the new one.

### 5. Start ProcessForge

Start the ProcessForge program with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8010
```

Leave this window open — it needs to keep running while you use ProcessForge. It
listens on your own computer at `http://127.0.0.1:8010`. (Port `8010`, not the more
common `8000` — chosen specifically so ProcessForge doesn't clash with other things
you might already have running locally, like NEXUS. If `8010` is ever busy too, pick
any other unused number and use it consistently in place of `8010` everywhere below.)
Press `Ctrl+C` in that window whenever you want to stop it.

### Even easier: the desktop tray app and setup tool (optional)

If you build the desktop helpers described in `desktop/README.md`, you get two
double-clickable `.exe` files that do steps 4 and 5 above without typing any
commands:

- **`ProcessForgeTray.exe`** — sits in your system tray and lets you
  start/stop/restart the ProcessForge server with a click, instead of running
  the `uvicorn` command by hand. Leave it running the same way you'd leave the
  command window open.
- **`ProcessForgeSetup.exe`** — opens a small window with username/password
  fields. **Create account** makes your operator account (step 4 above);
  **Update password** changes the password for an account that already exists —
  instead of running the `auth.users create` / `auth.users passwd` command-line
  tools. (Changing a password here signs that account out everywhere, same as
  the command-line tool.)

These are optional and personal to this machine (see `desktop/README.md` for
why they aren't redistributable) — the CLI commands above remain the
documented, always-available way to do the same things.

---

## Using the website (the easy way)

**Open a web browser and go to `http://127.0.0.1:8010/ui/login`.** This is the
easiest way to use ProcessForge — everything described in this manual (starting a
conversation, approving a recommendation, building an automation, checking the
audit log, deleting a business) has a page for it. Log in with the username and
password you created in step 4, and a menu at the top of every page takes you
where you need to go.

The rest of this manual also documents the command-line (`curl.exe`) way of doing
the exact same things, one step at a time — useful if you want to script something,
automate a repeated task, or just don't have a browser handy. **You don't need to
read the rest of this manual to use ProcessForge day to day** — the website covers
everything. Keep reading if you want the command-line details, or skip ahead to
"What's coming next".

**The login page, the dashboard, the interview conversation page, the
transcript page, and now the recommendation page too each start with a short
line of plain text explaining what that page is for**, and a line telling you
exactly what to do next (e.g. "enter your username and password, then select
Log In"). On the recommendation page, that "what to do next" line changes
depending on where the recommendation is in its journey — "review the ROI
and summary, then select Approve" while it's a draft, "select Build to
generate the automation" once it's approved, and "review the automation and
submit feedback if changes are needed" once it's built. The recommendation
page also now shows the recommendation's **estimated time savings (ROI)**
and its status in bold, right at the top, so you don't have to hunt for
them. This is an ongoing effort to make every page on the website explain
itself without needing this manual open — the remaining pages (the audit
log and the delete-a-business page) will get the same treatment in later
updates.

---

## Logging in (command-line way)

*Using the website instead? Just go to `http://127.0.0.1:8010/ui/login` and log in
there — skip this section.*

Before you can do anything else, you need to log in and get a **token** — a
temporary pass that proves who you are. You send your username and password once,
and get back a token to use for everything else. Each token works for 7 days, then
you'll need to log in again.

```powershell
curl.exe -s -X POST http://127.0.0.1:8010/auth/login -H "Content-Type: application/json" -d "{\"username\": \"YOUR_USERNAME\", \"password\": \"YOUR_PASSWORD\"}"
```

You'll get back something like `{"token": "a-long-random-string"}`. Copy that
string — you'll use it in place of `YOUR_TOKEN_HERE` in every example below. If
you're done for the day and want to make sure that token can't be used by anyone
else, log out with it:

```powershell
curl.exe -s -X POST http://127.0.0.1:8010/auth/logout -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

---

## How to use ProcessForge (running a session) — command-line way

*Using the website? The dashboard page (`http://127.0.0.1:8010/ui`) does this with
a form — skip this section.*

Once ProcessForge is running (step 5 above) and you're logged in (previous
section), you talk to it by sending it a request. The address you send that
request to is `/sessions`. Here's a copy-pasteable example using `curl.exe` (a
simple command-line tool for sending requests), which you can run from a *second*
terminal window while ProcessForge is still running in the first.

To avoid quoting problems that can happen when different versions of PowerShell
handle quote marks differently, first save the request's contents to a small file:

```powershell
@'
{
  "business_name": "Acme Bookkeeping",
  "tenant": "acme",
  "answers": [
    "We manually reconcile invoices every week.",
    "It takes about 3 hours and we do it weekly.",
    "We would like it to happen automatically."
  ]
}
'@ | Set-Content -Path session.json -Encoding utf8
```

Then send it:

```powershell
curl.exe -X POST http://127.0.0.1:8010/sessions `
  -H "Authorization: Bearer YOUR_TOKEN_HERE" `
  -H "Content-Type: application/json" `
  -d "@session.json"
```

Here's what each part means:

- **The first block** — writes the details of the task you want to describe into a
  file named `session.json` sitting next to it. Edit the `business_name`, `tenant`,
  and `answers` values to describe your own task before saving.
- **`http://127.0.0.1:8010/sessions`** — the address of the ProcessForge program
  running on your own computer.
- **`Authorization: Bearer YOUR_TOKEN_HERE`** — replace `YOUR_TOKEN_HERE` with the
  token you got back from logging in (see "Logging in" above). This is required —
  without a valid, non-expired token, ProcessForge rejects your request.
- **`-d "@session.json"`** — tells `curl.exe` to send the contents of the file you
  just saved as the request.
- **`business_name`** — the name of the business this task belongs to.
- **`tenant`** — which client or company this is for. One ProcessForge setup can be
  used for many different clients/companies, and this tells it which one you mean.
- **`answers`** — a list of things you type describing the task. If you've connected
  an AI service, it reads all of this to figure out the details. If not (or if the AI
  call fails), ProcessForge falls back to only using the first item as the task
  description and the last item as the outcome you want, scanning what's in between
  for time/frequency clues, as explained above.

### What you get back

ProcessForge replies with:

- Which business and session this was for.
- How many tasks it found (today, this is always exactly one).
- One or more **opportunities** — each one is a task it identified, along with an
  estimated range of hours you could save (a low and a high number, not one exact
  figure), some notes on what assumptions it made, and how confident it is.
- One or more **recommendations** — a short written summary of what it suggests,
  and its approval status. Nothing is built or run automatically — a person still
  has to review and approve each recommendation before anything happens.

---

## Having a real conversation (instead of typing everything at once) — command-line way

*Using the website? The dashboard page starts this automatically and the
conversation happens right on screen — skip this section.*

Instead of `/sessions` (where you write out everything up front), you can have
ProcessForge ask you questions one at a time.

**1. Start the conversation:**

```powershell
curl.exe -s -X POST http://127.0.0.1:8010/interviews -H "Authorization: Bearer YOUR_TOKEN_HERE" -H "Content-Type: application/json" -d "{\"business_name\": \"Acme Bookkeeping\", \"tenant\": \"acme\"}"
```

You'll get back a `session_id` and a `question` — the first thing ProcessForge wants
to know. Read the question, decide your answer.

**2. Answer it, and keep answering until it says it's done:**

```powershell
curl.exe -s -X POST "http://127.0.0.1:8010/interviews/THE_SESSION_ID/answer?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE" -H "Content-Type: application/json" -d "{\"answer\": \"We manually reconcile invoices every week.\"}"
```

Each time, you'll get back one of two things:
- **Another `question`** — answer it the same way, sending it back to the same
  `/interviews/THE_SESSION_ID/answer` address.
- **The final result** — the same shape you'd get from `/sessions` (the task it
  found, the estimated hours saved, and a draft recommendation). This means the
  conversation is over — you don't need to send another answer to this session.

Without an AI service connected, this always takes exactly 6 answers (a fixed,
predictable set of questions: how long/how often, desired outcome, where the input
files or source data live, any filter rules or specific column values that matter,
and the desired output format). With one connected, ProcessForge decides on its own
when it has enough information — it will never ask more than 6 questions total,
even if it would otherwise keep going, so a conversation can't run forever.

**Once a conversation is finished, that session is done** — sending another answer
to it gets refused (an error saying the interview is already complete), the same
way trying to build an automation twice or delete the same business twice does.

Everything else about a finished conversation — approving the recommendation,
building the automation, giving feedback — works exactly the same as described
above for `/sessions`.

**Want to re-read the whole conversation?** At any point (during or after the
conversation), you can fetch the full back-and-forth — every question ProcessForge
asked and every answer you gave, in order:

```powershell
curl.exe -s "http://127.0.0.1:8010/interviews/THE_SESSION_ID/transcript?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

This is read-only — it doesn't change anything, so it's safe to check as often as
you like.

*Using the website? Go to
`http://127.0.0.1:8010/ui/interview/THE_SESSION_ID/transcript?tenant=acme` to see
the same conversation laid out as a page instead of raw text — skip the command
above. Or, from the recommendation's own page, click the **"View interview
transcript"** link near the top — it takes you straight there and only appears
when ProcessForge can tell which conversation the recommendation came from.*

---

## Approving a recommendation and building the automation — command-line way

*Using the website? Open the recommendation's page
(`http://127.0.0.1:8010/ui/recommendations/THE_ID?tenant=acme`) and use the buttons
— skip this section.*

Every recommendation starts out as a **draft** — nothing happens until a person
approves it. Once you have a recommendation's ID (from the reply above), here's how
to move it forward. All of these need the same login token header as before
(`Authorization: Bearer YOUR_TOKEN_HERE`) and a `tenant` value telling
ProcessForge which client/company this is for.

1. **Look up a recommendation** — check its current summary and approval status:

   ```powershell
   curl.exe -s "http://127.0.0.1:8010/recommendations/THE_ID?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE"
   ```

   If ProcessForge can tell which conversation this recommendation came from, the
   reply also includes a `session_id` — that's what powers the "View interview
   transcript" link on the recommendation's page mentioned earlier. If ProcessForge
   can also work out the estimated time savings, the reply includes `roi_low_hrs`
   and `roi_high_hrs` — the low and high ends of the estimated hours saved per
   year, the same range shown prominently near the top of the recommendation's
   page on the website.

2. **Approve it** — this is the "yes, go ahead" step. Nothing is built yet — this
   just marks the recommendation as approved:

   ```powershell
   curl.exe -s -X POST "http://127.0.0.1:8010/recommendations/THE_ID/approve?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE"
   ```

3. **Build it** — this actually creates the automation (a written plan of what it
   would do, what it could affect if something went wrong, and how to undo it — it
   does NOT run anything against your real systems, it only produces a plan for a
   person to look at). This step only works AFTER you've approved the
   recommendation — if you try it before approving, ProcessForge refuses and tells
   you so, instead of building something nobody signed off on:

   ```powershell
   curl.exe -s -X POST "http://127.0.0.1:8010/recommendations/THE_ID/build?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE"
   ```

   You'll get back an **automation** — its plan, what it could affect ("blast
   radius"), and how to undo it ("rollback"). Like a recommendation, an automation
   also starts out needing its own separate approval before it's ever actually
   used — building it is not the same as running it.

   The plan also includes a **handoff** section — a plain summary of what
   ProcessForge already knows about the task (from your earlier answers), a list
   of **open questions** it couldn't figure out on its own (things like "where does
   the input file live?" that nobody has told it yet), and a **suggested approach**
   (the steps it's proposing). This is put together from the information you
   already gave it — nothing here is guessed or invented by an AI; if a question
   shows up, it's because that detail genuinely hasn't been captured yet, and
   someone should fill it in before this automation is actually built for real.

   If your interview answered where the input files live, what filter rules or
   column values matter, and what output format you want, those three answers
   now show up as known facts in this handoff automatically — and the matching
   "where does the input file live" open question disappears, since it's no
   longer actually open. This matching is done by simple, predictable text
   matching against the question that was asked (never guessed by an AI) — if
   an answer can't be confidently matched to one of those three questions, it's
   left out rather than risk pairing it with the wrong question.



4. **Give feedback and get a revised version** — if the automation isn't quite
   right, describe what should change, and ProcessForge produces a new, revised
   version (the original is kept; this creates a new one, it doesn't overwrite
   anything):

   ```powershell
   curl.exe -s -X POST "http://127.0.0.1:8010/automations/THE_AUTOMATION_ID/feedback?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE" -H "Content-Type: application/json" -d "{\"feedback\": \"Please narrow this to only the invoicing system.\"}"
   ```

5. **Answer one of the handoff's open questions and get an updated plan** — if the
   handoff came back with an open question you can now answer (e.g. "where does the
   input file live for this task?"), send the question and your answer back to the
   recommendation itself and ProcessForge regenerates the handoff with your answer
   folded in — as a new automation version, same as feedback above (the original is
   kept untouched, this creates a new one):

   ```powershell
   curl.exe -s -X POST "http://127.0.0.1:8010/recommendations/THE_ID/refine?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE" -H "Content-Type: application/json" -d "{\"turns\": [{\"question\": \"Where does the input file live for this task?\", \"answer\": \"It comes from the shared drive's nightly export folder.\"}]}"
   ```

   You can send more than one question/answer pair at once in the `turns` list.
   The reply is a new automation, with the answered question no longer showing up
   in its open questions (if ProcessForge could match your answer to one of the
   three questions it already knows how to recognize: where the input file lives,
   filter rules or column values, and desired output format — an answer to a
   different question is simply not matched to anything, rather than guessed).
   If you submit answers this way but ProcessForge can't find the underlying
   interview to attach them to, it reports an error instead of quietly
   ignoring your answers and returning what looks like a normal new version.

6. **Link a product/tool you found for this automation** — once you've found an
   existing product or tool (a Zapier recipe, a vendor's app, etc.) that matches
   what this automation needs, save a link to it on the automation record so it's
   easy to find again later, along with an optional note about why it's a good
   fit:

   ```powershell
   curl.exe -s -X POST "http://127.0.0.1:8010/automations/THE_AUTOMATION_ID/link?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE" -H "Content-Type: application/json" -d "{\"product_url\": \"https://example.com/some-product\", \"product_notes\": \"Handles the CSV export step out of the box.\"}"
   ```

   `product_notes` is optional — you can leave it out and just save the link.
   The link must start with `http://` or `https://`; anything else (a typo, or a
   web address that's missing a scheme) is rejected with an error rather than
   saved, since a bad link here would otherwise show up as a broken or unsafe
   clickable link on the website. This doesn't run or open the link — it just
   stores the address as text for a person to click later.

   *Using the website?* Once you've saved a link this way, open the
   recommendation's page (`http://127.0.0.1:8010/ui/recommendations/THE_ID?tenant=acme`)
   and scroll to the automation section — a "Built product" area appears there
   showing the link as a real clickable link (opens in a new tab), plus your
   notes underneath if you added any. If no link has been saved yet, this area
   simply doesn't show up. As an extra safety check on top of the one already
   done when you saved the link, the website double-checks the address really
   does start with `http://` or `https://` before turning it into a clickable
   link — if that check ever somehow failed, it would show the address as
   plain text instead of a link, rather than risk a broken or unsafe one.

**A note on privacy between clients:** if you try to look up, approve, or build
something using the wrong `tenant` value (e.g. a typo, or accidentally mixing up
two clients), ProcessForge treats it exactly the same as if that ID didn't exist at
all — it won't tell you "wrong client," just "not found." This is deliberate: it
means one client's data can never accidentally leak into what another client can
see, not even a hint that it exists.

---

## Seeing who approved what (the audit log) — command-line way

*Using the website? Go to `http://127.0.0.1:8010/ui/audit-log` — skip this section.*

Every time someone approves a recommendation, ProcessForge permanently records who
did it and when — this record can never be edited or deleted, even by ProcessForge
itself (it's a "write once, keep forever" log, the same idea as a bank keeping a
permanent record of transactions). To see it for a client:

```powershell
curl.exe -s "http://127.0.0.1:8010/audit-log?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

Add `&record_id=THE_ID` to the address to see only the history for one specific
recommendation.

---

## Permanently deleting a client's data — command-line way

*Using the website? Go to `http://127.0.0.1:8010/ui/businesses/delete` — the same
"type the ID again to confirm" safeguard is there too — skip this section.*

If a client asks you to delete everything ProcessForge has stored about them, this
removes it all — every task, estimate, recommendation, and automation for that
business — permanently and all at once. **This cannot be undone.**

Because this is irreversible, ProcessForge requires you to type the business's ID
a second time as a confirmation, so a typo or an accidental click can't delete the
wrong thing (or delete anything at all, if the two don't match exactly):

```powershell
curl.exe -s -X POST "http://127.0.0.1:8010/businesses/THE_BUSINESS_ID/delete?tenant=acme" -H "Authorization: Bearer YOUR_TOKEN_HERE" -H "Content-Type: application/json" -d "{\"confirm_business_id\": \"THE_BUSINESS_ID\"}"
```

Both `THE_BUSINESS_ID` occurrences must be exactly the same business ID — if they
don't match, ProcessForge refuses and deletes nothing. You'll get back a count of
exactly what was removed. (The approval history/audit log for that business, above,
is the one thing that is NOT deleted — a permanent record is supposed to survive
even the thing it recorded, the same way closing a bank account doesn't erase your
past transaction history with that bank.)

---

## What's coming next

Adding the same short "what this page is for" / "what to do next" text (see
"Using the website" above) to the remaining website pages that don't have it
yet: a recommendation's page, the audit log page, and the delete-a-business
page. The login page, dashboard, interview conversation page, and transcript
page already have it.

---

## Where to get more help

This manual only covers using ProcessForge day to day. If you're a developer, or you
want to understand exactly how ProcessForge works under the hood — its data
structures, its security model, its build history — read `CLAUDE.md` in this same
folder. That file is the technical source of truth for this project.
