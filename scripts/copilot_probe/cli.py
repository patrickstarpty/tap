"""Deterministic capability report; real provider execution remains gated."""

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from scripts.copilot_probe.evaluate import (
    CAPABILITIES,
    REQUIRED_CAPABILITIES,
    capability_matrix,
    ready_for_runtime,
)

_SCENARIOS = (*CAPABILITIES, "matrix")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copilot capability probe (real execution awaits verified meter and terminal schema)"
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
        "Model-call meter and terminal event schema are not verified; real execution blocked."
        if opted_in
        else "Real probe requires both opt-in flags; execution skipped."
    )
    # Never launch a provider call until the model budget and terminal state can be verified.
    report = {
        "scenario": args.scenario,
        "status": "unverified",
        "execution": "blocked" if opted_in else "skipped",
        "reason": reason,
        "budget_enforced": False,
        "model_calls_used": None,
        "max_model_calls": args.max_model_calls,
        "max_seconds": args.max_seconds,
    }
    if args.scenario == "matrix":
        matrix = capability_matrix(())
        report["capabilities"] = [asdict(result) for result in matrix.values()]
        report["required_capabilities"] = sorted(REQUIRED_CAPABILITIES)
        report["ready_for_runtime"] = ready_for_runtime(
            tuple(matrix.values()), REQUIRED_CAPABILITIES
        )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"{args.scenario}: unverified (execution {report['execution']})")
    return 3 if opted_in else 0


if __name__ == "__main__":
    raise SystemExit(main())
