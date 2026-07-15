"""Per-iteration build-session logger — mirrors council loop progress to an external
build-log webhook (§6.1).

This logs the BUILD PROCESS only, never product/tenant data. Fire-and-forget: if the
endpoint is unreachable, warn on stderr and return False — callers must never let this
block or fail the build loop.

Wiring: intended to be invoked by whatever drives council iterations for this repo as
a post-iteration step, after the Realist verdict is written to .council/state/.
Callable standalone:

    python tools/brain_log.py '{"loop": 1, "iteration": 3, "goal": "...", "verdict": "ACCEPT", ...}'
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.error

BUILD_LOG_URL = os.environ.get("BUILD_LOG_URL")
BUILD_LOG_PATH_PREFIX = os.environ.get("BUILD_LOG_PATH_PREFIX", "processforge/build-log")


def render_note(iteration: dict) -> str:
    lines = [
        f"# ProcessForge build — loop {iteration['loop']} iteration {iteration['iteration']}",
        "",
        f"- **Timestamp:** {iteration['timestamp']}",
        f"- **Goal:** {iteration['goal']}",
        f"- **Verdict:** {iteration['verdict']}",
        f"- **Commit:** {iteration.get('commit_hash') or '(not committed)'}",
        f"- **Seam test:** {iteration.get('seam_test_result', 'n/a')}",
        f"- **pip-audit:** {iteration.get('pip_audit_result', 'n/a')}",
        "",
        "## Engineer summary",
        iteration.get("engineer_summary", ""),
        "",
        "## Files touched",
        *(f"- {f}" for f in iteration.get("files_touched", [])),
    ]
    return "\n".join(lines)


def log_iteration(iteration: dict) -> bool:
    """Best-effort POST to the build-log webhook. Returns True on success, False otherwise. Never raises."""
    token = os.environ.get("BUILD_LOG_TOKEN")
    if not BUILD_LOG_URL or not token:
        print("brain_log: BUILD_LOG_URL/BUILD_LOG_TOKEN not set, skipping", file=sys.stderr)
        return False
    body = json.dumps({
        "content": render_note(iteration),
        "filename": f"{BUILD_LOG_PATH_PREFIX}/{iteration['timestamp']}-loop{iteration['loop']}-iter{iteration['iteration']}.md",
    }).encode("utf-8")
    req = urllib.request.Request(
        BUILD_LOG_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"brain_log: build-log endpoint unreachable, continuing build ({e})", file=sys.stderr)
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: brain_log.py '<json iteration payload>'", file=sys.stderr)
        sys.exit(1)
    ok = log_iteration(json.loads(sys.argv[1]))
    sys.exit(0 if ok else 0)  # never fail the build loop on a missing/unreachable endpoint
