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

**Honesty check on today's behavior:** right now, ProcessForge does *not* have a
back-and-forth conversation with you. It only looks at the first line of what you
typed (as the description of the task) and the last line (as the outcome you want).
Everything in between is only scanned for those time/frequency clues. Each time you
use it, you get back exactly one task, one estimate, and one draft recommendation —
not a full interview yet. That smarter, more conversational version is planned but
not built yet (see "What's coming next" below).

Also: even if you set up a connection to an AI service (see Setup, step 3), that
doesn't change any of the above yet. Right now, ProcessForge doesn't actually call
an AI to do any of this — it does it all with straightforward, predictable rules.
Setting up an AI connection now just gets the wiring ready so a future update can
use it. It changes nothing about what you get back today.

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

Then open `.env` in a text editor and fill in these two values — they're the only
ones you truly need right now:

- **`PROCESSFORGE_DB_PATH`** — this is where ProcessForge stores its data (the tasks,
  estimates, and recommendations it creates) on your computer, as a file. The
  example default (`./kb/processforge.db`) is fine to start with.
- **`PROCESSFORGE_API_TOKEN`** — this is the password anyone (including you) needs
  to provide to use ProcessForge. Pick any password-like string of your own — there's
  no required format, just make it something not easily guessed. **This one matters:
  if you leave it blank, ProcessForge will refuse every single request you send it**
  (you'll get an error that says you're "not authenticated"). You must set a real
  value here before ProcessForge will do anything for you.

Everything else in `.env` (the lines starting with `PROCESSFORGE_LLM_`,
`PROCESSFORGE_MODEL_`, and `PROCESSFORGE_OLLAMA_HOST`) is about connecting an AI
service. You can leave those blank for now — as explained above, nothing calls an AI
yet, so those settings don't affect anything you'll see today. `BUILD_LOG_URL` and
`BUILD_LOG_TOKEN` are only used by the developers building ProcessForge itself; you
can leave those blank too.

### 3. Check that everything is set up correctly

Before you start using ProcessForge, run this quick check. It confirms that
everything installed correctly and that nothing is broken:

```powershell
.\run-tests.ps1
```

If this finishes without errors, your setup is good. If it reports a problem, fix
that before continuing — don't try to use ProcessForge with a failing check.

### 4. Start ProcessForge

Start the ProcessForge program with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app
```

Leave this window open — it needs to keep running while you use ProcessForge. By
default it listens on your own computer at `http://127.0.0.1:8000`. Press `Ctrl+C`
in that window whenever you want to stop it.

---

## How to use ProcessForge (running a session)

Once ProcessForge is running (step 4 above), you talk to it by sending it a request.
The address you send that request to is `/sessions`. Here's a copy-pasteable example
using `curl.exe` (a simple command-line tool for sending requests), which you can run
from a *second* terminal window while ProcessForge is still running in the first:

```powershell
curl.exe -X POST http://127.0.0.1:8000/sessions `
  -H "Authorization: Bearer YOUR_PASSWORD_HERE" `
  -H "Content-Type: application/json" `
  -d '{
    "business_name": "Acme Bookkeeping",
    "tenant": "acme",
    "answers": [
      "We manually reconcile invoices every week.",
      "It takes about 3 hours and we do it weekly.",
      "We would like it to happen automatically."
    ]
  }'
```

Here's what each part means:

- **`http://127.0.0.1:8000/sessions`** — the address of the ProcessForge program
  running on your own computer.
- **`Authorization: Bearer YOUR_PASSWORD_HERE`** — replace `YOUR_PASSWORD_HERE` with
  the exact value you put in `PROCESSFORGE_API_TOKEN` in your `.env` file. This is
  required — without it, ProcessForge rejects your request.
- **`business_name`** — the name of the business this task belongs to.
- **`tenant`** — which client or company this is for. One ProcessForge setup can be
  used for many different clients/companies, and this tells it which one you mean.
- **`answers`** — a list of things you type describing the task. Remember: only the
  first item is used as the task description, and only the last item is used as the
  outcome you want — the ones in between are just scanned for time/frequency clues,
  as explained above.

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

## What's coming next

These are known, planned improvements — not promises of a specific date:

- **A real back-and-forth interview.** Right now ProcessForge only reads the first
  and last things you type. A future version will actually ask you follow-up
  questions instead of just picking apart what you type in one go.
- **A real login system.** Right now everyone who uses a given ProcessForge setup
  shares one single password. A future version will give each person or client
  their own separate login.
- **Actually connecting the AI service.** Right now, setting up an AI provider (see
  Setup, step 2) doesn't change any behavior — ProcessForge doesn't call it yet. A
  future version will use it for smarter task understanding.

---

## Where to get more help

This manual only covers using ProcessForge day to day. If you're a developer, or you
want to understand exactly how ProcessForge works under the hood — its data
structures, its security model, its build history — read `CLAUDE.md` in this same
folder. That file is the technical source of truth for this project.
