"""Count roles in all_jobs.json that still need an AI triage verdict.

Cheap, dependency-free gate for .github/workflows/triage.yml: when nothing is
unscored the workflow skips installing SDKs and calling the model, so the
event-driven (workflow_run) triggers cost nothing on quiet runs.

Prints the count and, when $GITHUB_OUTPUT is set, appends ``unscored=<n>``.
Never needs API keys or candidate secrets.
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import triage_agent  # noqa: E402  (stdlib-only at import time)


def count_unscored() -> int:
    if not os.path.exists(triage_agent.ALL_JOBS_PATH):
        return 0
    jobs = triage_agent.load_jobs(from_files=False)
    scores = triage_agent.load_scores().get("scores", {})
    return len(triage_agent.find_unscored(jobs, scores))


def main() -> int:
    n = count_unscored()
    print(f"unscored roles: {n}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"unscored={n}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
