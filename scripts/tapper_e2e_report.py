"""Validate native Playwright results and export only a closed non-content projection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from typing import Any


def project_report(
    phase: str, native: dict[str, Any], raw: bytes, manifest: dict[str, list[str]]
) -> dict[str, Any]:
    expected = manifest[phase]
    names = {Path(name).name: name for name in expected}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def suites(values: list[dict[str, Any]]) -> None:
        for suite in values:
            for spec in suite.get("specs", []):
                filename = spec.get("file", suite.get("file"))
                if filename not in names:
                    # Native versions can return tests/e2e-prefixed file paths.
                    if filename not in expected:
                        raise ValueError("unexpected native spec")
                    file = filename
                else:
                    file = names[filename]
                title = spec.get("title")
                if (
                    not isinstance(title, str)
                    or not 0 < len(title) <= 256
                    or any(ord(c) < 32 for c in title)
                ):
                    raise ValueError("invalid native test identity")
                tests = []
                for test in spec.get("tests", []):
                    results = test.get("results", [])
                    if (
                        test.get("expectedStatus") != "passed"
                        or test.get("status") != "expected"
                        or len(results) != 1
                        or results[0].get("status") != "passed"
                        or results[0].get("retry") != 0
                    ):
                        raise ValueError(
                            "native test skipped, failed, flaky or retried"
                        )
                    tests.append(
                        {
                            "project": test.get("projectName"),
                            "expectedStatus": "passed",
                            "status": "expected",
                            "results": [{"status": "passed", "retry": 0}],
                        }
                    )
                if not tests or any(t["project"] != "chromium" for t in tests):
                    raise ValueError("native project missing")
                rows.append({"file": file, "title": title, "tests": tests})
                seen.add(file)
            suites(suite.get("suites", []))

    suites(native.get("suites", []))
    count = sum(len(row["tests"]) for row in rows)
    stats = native.get("stats", {})
    if (
        seen != set(expected)
        or count == 0
        or stats.get("expected") != count
        or any(stats.get(key) != 0 for key in ("unexpected", "flaky", "skipped"))
        or native.get("errors")
    ):
        raise ValueError("native report incomplete")
    return {
        "schemaVersion": 1,
        "phase": phase,
        "nativeSha256": hashlib.sha256(raw).hexdigest(),
        "specifications": rows,
        "counts": {"passed": count, "failed": 0, "flaky": 0, "skipped": 0},
        "cleanup": "pending",
    }


VERIFY_SPEC = "apps/backend/tests/integration/test_tapper_persistence_restart.py"


def project_pytest(raw: bytes) -> dict[str, Any]:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("invalid native XML")
    root = ET.fromstring(raw)
    suites = list(root) if root.tag == "testsuites" else [root]
    rows = []
    count = 0
    for suite in suites:
        if suite.tag != "testsuite" or any(
            int(suite.get(k, "-1")) != 0 for k in ("errors", "failures", "skipped")
        ):
            raise ValueError("native pytest failure or skip")
        cases = suite.findall("testcase")
        if len(cases) != int(suite.get("tests", "-1")):
            raise ValueError("native pytest count mismatch")
        for case in cases:
            name = case.get("name", "")
            classname = case.get("classname", "")
            if (
                not classname.endswith(
                    "tests.integration.test_tapper_persistence_restart"
                )
                or not name
                or len(name) > 256
                or any(ord(c) < 32 for c in name)
                or any(
                    child.tag
                    in {"failure", "error", "skipped", "rerunFailure", "rerunError"}
                    for child in case
                )
            ):
                raise ValueError("invalid native pytest identity or outcome")
            rows.append(
                {
                    "file": VERIFY_SPEC,
                    "title": name,
                    "classname": classname,
                    "tests": [
                        {
                            "project": "pytest",
                            "expectedStatus": "passed",
                            "status": "expected",
                            "results": [{"status": "passed", "retry": 0}],
                        }
                    ],
                }
            )
            count += 1
    if count == 0 or not any(
        row["title"]
        == "test_exact_tapper_state_survives_application_and_compose_restarts"
        for row in rows
    ):
        raise ValueError("native persistence verification missing")
    return {
        "schemaVersion": 1,
        "phase": "verify",
        "nativeSha256": hashlib.sha256(raw).hexdigest(),
        "specifications": rows,
        "counts": {"passed": count, "failed": 0, "flaky": 0, "skipped": 0},
        "cleanup": "pending",
    }


def main() -> None:
    command = sys.argv[1]
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "scripts/tapper-e2e-specs.json").read_text())
    if command == "specs":
        print("\n".join(manifest[sys.argv[2]]))
    elif command == "validate":
        phase, path, destination = sys.argv[2:]
        raw = Path(path).read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("native report too large")
        projection = project_report(phase, json.loads(raw), raw, manifest)
        Path(destination).write_text(
            json.dumps(projection, sort_keys=True, indent=2) + "\n"
        )
    elif command == "export":
        state, destination, status = sys.argv[2:]
        if status not in {"complete", "failed"}:
            raise ValueError("invalid cleanup state")
        output = Path(destination)
        if output.is_symlink():
            raise ValueError("invalid export path")
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        for phase in [*manifest, "verify"]:
            target = output / ("phase-" + phase + ".json")
            if target.is_symlink():
                raise ValueError("invalid export file")
            target.unlink(missing_ok=True)
        for phase in [*manifest, "verify"]:
            source = Path(state) / (
                ("pytest-verify.xml")
                if phase == "verify"
                else ("playwright-" + phase + ".json")
            )
            if not source.is_file():
                continue
            raw = source.read_bytes()
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("native report too large")
            projection = (
                project_pytest(raw)
                if phase == "verify"
                else project_report(phase, json.loads(raw), raw, manifest)
            )
            projection["cleanup"] = status
            target = output / ("phase-" + phase + ".json")
            if target.is_symlink():
                raise ValueError("invalid export file")
            target.write_text(json.dumps(projection, sort_keys=True, indent=2) + "\n")
    elif command == "finish":
        destination, status = sys.argv[2:]
        if status not in {"complete", "failed"}:
            raise ValueError("invalid cleanup state")
        for phase in [*manifest, "verify"]:
            path = Path(destination) / ("phase-" + phase + ".json")
            if path.is_symlink():
                raise ValueError("invalid export file")
            if path.is_file():
                projection = json.loads(path.read_text())
                projection["cleanup"] = status
                path.write_text(json.dumps(projection, sort_keys=True, indent=2) + "\n")
    else:
        raise ValueError("invalid report operation")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, TypeError):
        raise SystemExit("E2E native evidence validation failed") from None
