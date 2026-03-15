#!/usr/bin/env python3
"""Security scan: compare bandit findings against the acknowledged baseline.

Exit 0 = no new findings.
Exit 1 = new findings detected (commit blocked).
Exit 2 = scan error.
"""

import hashlib
import json
import subprocess
import sys
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
        print(result.stderr, file=sys.stderr)
        sys.exit(2)


def load_baseline() -> dict:
    if BASELINE_FILE.exists():
        return json.loads(BASELINE_FILE.read_text())
    return {"acknowledged": {}}


def main() -> None:
    issues = run_bandit()
    baseline = load_baseline()
    acknowledged = set(baseline.get("acknowledged", {}).keys())

    current: dict[str, dict] = {fingerprint(i): i for i in issues}
    new_issues = {fp: i for fp, i in current.items() if fp not in acknowledged}

    if not new_issues:
        count = len(issues)
        if count:
            print(f"Security scan: {count} acknowledged issue(s) in baseline, none new.")
        else:
            print("Security scan: clean.")
        sys.exit(0)

    print("\nSecurity scan: NEW findings not in baseline:\n")
    for fp, issue in new_issues.items():
        sev = issue["issue_severity"]
        conf = issue["issue_confidence"]
        print(f"  [{fp}] {issue['test_id']} ({sev}/{conf})")
        print(f"    {issue['filename']}:{issue['line_number']}")
        print(f"    {issue['issue_text']}")
        print(f"    More info: {issue['more_info']}")
        print()

    print("To acknowledge these findings and allow the commit, run:")
    print("  uv run python scripts/update_security_baseline.py")
    print("Then stage .security-baseline.json and commit again.\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
