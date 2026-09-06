"""Verify an exact migration revision against frozen, nonempty 0005 data."""

import json
import sys

from migration_support import run_migration_gate

if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("one literal migration revision is required")
        report = run_migration_gate(sys.argv[1])
    except (Exception, KeyboardInterrupt) as error:
        report = {
            "status": "failed",
            "gate": "migration-check",
            "error": type(error).__name__,
        }
    print(json.dumps(report))
    raise SystemExit(0 if report["status"] == "passed" else 1)
