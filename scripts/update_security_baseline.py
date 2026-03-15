#!/usr/bin/env python3
"""Update .security-baseline.json with all current bandit findings.

Run this to acknowledge new security issues so commits can proceed.
The updated baseline must be staged and committed alongside your changes.
"""

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

BASELINE_FILE = Path(".security-baseline.json")
SCAN_TARGET = "src/"


def fingerprint(issue: dict) -> str:
    key = f"{issue['test_id']}:{issue['filename']}:{issue['code'].strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def run_bandit() -> list[dict]:
    result = subprocess.run(
        ["bandit", "-r", SCAN_TARGET, "-f", "json", "-q"],
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(result.stdout).get("results", [])
    except json.JSONDecodeError:
        print("ERROR: Could not parse bandit output", file=sys.stderr)
        sys.exit(2)


def main() -> None:
    issues = run_bandit()

    existing: dict = {}
    if BASELINE_FILE.exists():
        existing = json.loads(BASELINE_FILE.read_text()).get("acknowledged", {})

    acknowledged: dict = {}
    for issue in issues:
        fp = fingerprint(issue)
        acknowledged[fp] = {
            "test_id": issue["test_id"],
            "filename": issue["filename"],
            "line": issue["line_number"],
            "severity": issue["issue_severity"],
            "confidence": issue["issue_confidence"],
            "text": issue["issue_text"],
            # Preserve original acknowledgement date if already in baseline
            "acknowledged_at": existing.get(fp, {}).get("acknowledged_at") or str(date.today()),
        }

    BASELINE_FILE.write_text(json.dumps({"acknowledged": acknowledged}, indent=2) + "\n")

    new_count = len(set(acknowledged) - set(existing))
    removed_count = len(set(existing) - set(acknowledged))
    print(
        f"Baseline updated: {len(acknowledged)} issue(s) acknowledged "
        f"({new_count} new, {removed_count} resolved)."
    )
    print(f"Stage and commit {BASELINE_FILE} to allow future commits to proceed.")


if __name__ == "__main__":
    main()
