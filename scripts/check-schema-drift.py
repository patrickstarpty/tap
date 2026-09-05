"""Compare authoritative metadata with a newly migrated disposable MySQL."""

import json

from migration_support import run_schema_gate

if __name__ == "__main__":
    try:
        report = run_schema_gate()
    except (Exception, KeyboardInterrupt) as error:
        report = {
            "status": "failed",
            "gate": "schema-drift",
            "error": type(error).__name__,
        }
    print(json.dumps(report))
    raise SystemExit(0 if report["status"] == "passed" else 1)
