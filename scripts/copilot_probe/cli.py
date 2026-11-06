"""Task 0.2 CLI boundary; real capability execution belongs to Task 0.3."""

import argparse
import json
import os
from pathlib import Path

_SCENARIOS = (
    "simple",
    "byok_all_calls",
    "child_identity",
    "parallel",
    "permissions",
    "control_receipt",
    "resume_workspace",
    "reconcile",
    "roster_scale",
    "config_freeze",
)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copilot capability probe (real adapter pending)"
    )
    parser.add_argument("--scenario", required=True, choices=_SCENARIOS)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-seconds", type=positive_int, default=180)
    parser.add_argument("--max-model-calls", type=positive_int, default=10)
    args = parser.parse_args()
    opted_in = (
        os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") == "1"
        and os.environ.get("TAP_RUN_COPILOT_PROBE") == "1"
    )
    reason = (
        "Model-call meter and terminal event schema are not verified; execution blocked."
        if opted_in
        else "Real probe requires both opt-in flags; execution skipped."
    )
    # No provider call is launched until Task 0.3 can enforce the model budget.
    report = {
        "scenario": args.scenario,
        "status": "unverified",
        "execution": "skipped",
        "reason": reason,
        "budget_enforced": False,
        "model_calls_used": None,
        "max_model_calls": args.max_model_calls,
        "max_seconds": args.max_seconds,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"{args.scenario}: unverified (execution skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
