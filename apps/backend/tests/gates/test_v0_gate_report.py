"""Inert native evidence fixtures: no service is launched by this test module."""

import copy
import hashlib
import importlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def gate():
    assert (ROOT / "scripts/tapper_v0_gate.py").is_file(), "V0 gate runner/report is missing"
    return importlib.import_module("scripts.tapper_v0_gate")


def junit(cases, *, skipped=0, failures=0, errors=0):
    suite = ET.Element(
        "testsuite",
        tests=str(len(cases)),
        skipped=str(skipped),
        failures=str(failures),
        errors=str(errors),
    )
    for classname, name, child in cases:
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if child:
            ET.SubElement(case, child)
    return ET.tostring(suite)


def test_native_junit_requires_every_selected_domain(gate):
    files = ["contract/test_origin_policy.py", "unit/access/test_scope_context.py"]
    raw = junit([("apps.backend.tests.contract.test_origin_policy", "test_origin", None)])
    with pytest.raises(ValueError):
        gate.parse_junit(raw, files)
    raw = junit(
        [
            ("apps.backend.tests.contract.test_origin_policy", "test_origin", None),
            ("apps.backend.tests.unit.access.test_scope_context", "test_scope", None),
        ]
    )
    assert gate.parse_junit(raw, files)["counts"] == {"passed": 2, "skipped": 0, "failed": 0}


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error", "rerunFailure", "rerunError"])
def test_native_junit_rejects_skip_xfail_xpass_and_retry_even_when_counts_claim_pass(gate, outcome):
    raw = junit([("apps.backend.tests.contract.test_origin_policy", "test_origin", outcome)])
    with pytest.raises(ValueError):
        gate.parse_junit(raw, ["contract/test_origin_policy.py"])


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"<testsuite",
        b"<!DOCTYPE x><testsuite/>",
        b'<testsuite tests="0" skipped="0" failures="0" errors="0"/>',
        b'<testsuite tests="1" skipped="0" failures="0" errors="0"/>',
    ],
)
def test_native_junit_rejects_missing_truncated_entity_and_empty_reports(gate, raw):
    with pytest.raises(ValueError):
        gate.parse_junit(raw, ["contract/test_origin_policy.py"])


def test_native_junit_rejects_missing_real_case_and_forged_module(gate):
    raw = junit([("evil.apps.backend.tests.contract.test_origin_policy", "test_origin", None)])
    with pytest.raises(ValueError):
        gate.parse_junit(raw, ["contract/test_origin_policy.py"])
    raw = junit([("apps.backend.tests.contract.test_origin_policy", "test_other", None)])
    with pytest.raises(ValueError):
        gate.parse_junit(
            raw,
            ["contract/test_origin_policy.py"],
            {"contract/test_origin_policy.py": ["test_origin"]},
        )


def phase_projection():
    return {
        "schemaVersion": 1,
        "phase": "app-restart",
        "nativeSha256": "a" * 64,
        "cleanup": "complete",
        "counts": {"passed": 1, "failed": 0, "flaky": 0, "skipped": 0},
        "specifications": [
            {
                "file": "tests/e2e/persistence.spec.ts",
                "title": "Tapper durable state survives the selected restart boundary",
                "tests": [
                    {
                        "project": "chromium",
                        "expectedStatus": "passed",
                        "status": "expected",
                        "results": [{"status": "passed", "retry": 0}],
                    }
                ],
            }
        ],
    }


def test_e2e_accepted_projection_preserves_native_count_and_identity(gate):
    assert gate.validate_e2e(phase_projection(), "app-restart")["passed"] == 1


@pytest.mark.parametrize(
    "drift", ["skip", "flaky", "retry", "missing", "phase", "cleanup", "digest", "count"]
)
def test_e2e_projection_cannot_hide_native_failure_or_missing_spec(gate, drift):
    value = phase_projection()
    if drift in {"skip", "flaky"}:
        value["counts"]["skipped" if drift == "skip" else "flaky"] = 1
    elif drift == "retry":
        value["specifications"][0]["tests"][0]["results"][0]["retry"] = 1
    elif drift == "missing":
        value["specifications"] = []
    elif drift == "phase":
        value["phase"] = "compose-restart"
    elif drift == "cleanup":
        value["cleanup"] = "pending"
    elif drift == "digest":
        value["nativeSha256"] = "caller-passed"
    else:
        value["counts"]["passed"] = 2
    with pytest.raises(ValueError):
        gate.validate_e2e(value, "app-restart")


def test_artifact_digest_and_containment_are_verified_from_bytes(gate, tmp_path):
    path = tmp_path / "result.json"
    path.write_bytes(b"{}")
    entry = {"path": "result.json", "bytes": 2, "sha256": hashlib.sha256(b"{}").hexdigest()}
    assert gate.read_artifact(tmp_path, entry) == b"{}"
    path.write_bytes(b"[]")
    with pytest.raises(ValueError):
        gate.read_artifact(tmp_path, entry)
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        gate.read_artifact(tmp_path, entry)
    entry["path"] = "../result.json"
    with pytest.raises(ValueError):
        gate.read_artifact(tmp_path, entry)


def test_owned_cleanup_requires_matching_started_and_terminal_resources(gate):
    events = [
        {"event": "owned-mysql", "identity": "tap-schema-0123456789ab", "state": state}
        for state in ["started", "complete"]
    ]
    assert gate.validate_ownership(events, "mysql", 1) == 1
    for changed in [
        events[:1],
        [events[1]],
        [events[0], {**events[1], "state": "failed"}],
        [events[0], {**events[1], "identity": "tap-schema-ffffffffffff"}],
    ]:
        with pytest.raises(ValueError):
            gate.validate_ownership(changed, "mysql", 1)


def test_planning_baseline_and_dirty_source_bytes_are_bound(gate):
    source = {
        "planningSha": "a54ab433eae52500683a5ff6ff9d79466a30e1ca",
        "head": "a" * 40,
        "dirty": True,
        "files": [{"path": "scripts/new.py", "mode": 0o644, "sha256": "b" * 64}],
    }
    gate.validate_provenance(source, copy.deepcopy(source))
    for field, value in [("planningSha", "a" * 40), ("head", "not-a-commit"), ("files", [])]:
        bad = {**source, field: value}
        with pytest.raises(ValueError):
            gate.validate_provenance(bad, bad)
    changed = copy.deepcopy(source)
    changed["files"][0]["sha256"] = "c" * 64
    with pytest.raises(ValueError):
        gate.validate_provenance(source, changed)


def test_report_cli_missing_planning_command_and_failed_migration_fail_without_resources(
    gate, tmp_path
):
    for report in [
        {},
        {"planningSha": "a54ab433eae52500683a5ff6ff9d79466a30e1ca", "commands": []},
        {"commands": [{"id": "migration-0009", "exitCode": 1}]},
    ]:
        path = tmp_path / "report.json"
        path.write_text(json.dumps(report))
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/tapper_v0_gate.py"), "validate", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "fail" in result.stdout.lower()


def schema_fixture(revision=None):
    value = {
        "status": "passed",
        "gate": "schema-drift",
        "tables": 21,
        "differences": [],
        "ownership": {
            "event": "owned-mysql",
            "identity": "tap-schema-0123456789ab",
            "state": "complete",
        },
    }
    if revision:
        value.update(
            gate="migration-check",
            revision=revision,
            preserved_rows={
                name: 1
                for name in [
                    "chat_turn",
                    "chat_event",
                    "turn_snapshot",
                    "outbox",
                    "knowledge_document",
                    "knowledge_document_revision",
                    "knowledge_ingestion_job",
                    "knowledge_chunk_manifest",
                    "knowledge_answer_snapshot",
                    "knowledge_citation_snapshot",
                    "knowledge_projection_state",
                    "knowledge_projection_fence",
                    "knowledge_projection_cleanup",
                    "knowledge_projection_lineage",
                ]
            },
            identity_seed={
                "enterprise": "local",
                "project": "tapper-demo",
                "actor": "tapper-local-user",
                "principal_type": "VALIDATION",
            },
            downgrade_replay="passed",
            scope_backfill="passed",
            constraints="passed",
            pre_ddl_rejection="passed",
            audit_constraints="passed",
            audit_downgrade_replay="passed",
            operations_constraints="passed",
            operations_downgrade_replay="passed",
        )
    return value


def test_actual_migration_identity_shape_and_failed_replay(gate):
    value = schema_fixture("0009_outbox_operations")
    gate.validate_schema(value, "0009_outbox_operations")
    value["operations_downgrade_replay"] = "failed"
    with pytest.raises(ValueError):
        gate.validate_schema(value, "0009_outbox_operations")


def complete_report(gate, tmp_path):
    commands = []
    own = [
        {"event": "owned-mysql", "identity": "tap-schema-0123456789ab", "state": state}
        for state in ["started", "complete"]
    ]
    for expected in gate.command_registry(tmp_path):
        row = {**expected, "cwd": str(ROOT), "exitCode": 0, "cleanup": "complete", "ownership": own}
        name = row["id"]
        (tmp_path / (name + ".log")).write_text("Native command completed.\n")
        row["log"] = gate.artifact(tmp_path, name + ".log")
        if row["kind"] == "schema":
            (tmp_path / (name + ".json")).write_text(
                json.dumps(schema_fixture(None if name == "schema" else name))
            )
            row["artifacts"] = [gate.artifact(tmp_path, name + ".json")]
        elif row["kind"] == "pytest":
            cases = []
            for file in gate.BUNDLES[name]:
                classname = "apps.backend.tests." + file[:-3].replace("/", ".")
                if file == "contract/test_validation_authorization_policy.py":
                    classname += ".TestValidationIdentityPolicy"
                if file == "contract/test_alternate_authorization_policy.py":
                    classname += ".TestAlternateIdentityPolicy"
                for title in gate.ANCHORS.get(file, ["test_fixture_behavior"]):
                    cases.append((classname, title, None))
            xml = ET.fromstring(junit(cases))
            events = own if name in {"project-audit", "recovery-operator"} else []
            if name == "recovery-operator":
                events = [
                    *events,
                    *[
                        {
                            "event": "owned-redis",
                            "identity": "tap-recovery-test-" + str(i) * 12,
                            "state": state,
                        }
                        for i in range(3)
                        for state in ["started", "complete"]
                    ],
                ]
            ET.SubElement(xml.find("testcase"), "system-err").text = "\n".join(
                json.dumps(e) for e in events
            )
            (tmp_path / (name + ".xml")).write_bytes(ET.tostring(xml))
            row["artifacts"] = [gate.artifact(tmp_path, name + ".xml")]
            if name == "storage":
                row["storageCleanup"] = {"minio": "complete", "azurite": "complete"}
                row["storageOwnership"] = {
                    "minio": {
                        "container": "a" * 64,
                        "volume": "owned-volume",
                        "owner": "b" * 32,
                        "image": "sha256:" + "c" * 64,
                        "state": "complete",
                    },
                    "azurite": {
                        "container": "d" * 64,
                        "project": "tap-task5-tests-0123456789ab",
                        "owner": "e" * 32,
                        "state": "complete",
                    },
                }
        else:
            (tmp_path / "e2e").mkdir()
            row["artifacts"] = []
            for phase, files in gate.PHASES.items():
                projection = phase_projection()
                projection.update(
                    phase=phase,
                    specifications=[],
                    counts={"passed": len(files), "failed": 0, "skipped": 0, "flaky": 0},
                )
                for file in files:
                    spec = copy.deepcopy(phase_projection()["specifications"][0])
                    spec.update(file=file, title=gate.E2E_TITLES[file])
                    if phase == "verify":
                        spec["tests"][0]["project"] = "pytest"
                    projection["specifications"].append(spec)
                path = "e2e/phase-" + phase + ".json"
                (tmp_path / path).write_text(json.dumps(projection))
                row["artifacts"].append(gate.artifact(tmp_path, path))
        commands.append(row)
    source = {
        "planningSha": "a54ab433eae52500683a5ff6ff9d79466a30e1ca",
        "head": "a" * 40,
        "dirty": True,
        "files": [{"path": "scripts/gate.py", "mode": 0o644, "sha256": "b" * 64}],
    }
    return {
        "schemaVersion": 1,
        "sourceBefore": source,
        "sourceAfter": copy.deepcopy(source),
        "registrySha256": gate.digest(gate.command_registry(tmp_path)),
        "commands": commands,
        "cleanup": "complete",
    }


def test_complete_native_report_requires_all_eleven_commands(gate, tmp_path):
    report = complete_report(gate, tmp_path)
    assert len(report["commands"]) == 11
    assert gate.validate_report(tmp_path, report)["passed"] > 20


@pytest.mark.parametrize(
    "drift",
    [
        "planning",
        "omitted",
        "duplicate",
        "migration",
        "skip",
        "artifact",
        "cleanup",
        "argv",
        "source",
    ],
)
def test_complete_report_cannot_promote_partial_or_failed_evidence(gate, tmp_path, drift):
    report = complete_report(gate, tmp_path)
    if drift == "planning":
        report["sourceBefore"].pop("planningSha")
    elif drift == "omitted":
        report["commands"].pop(2)
    elif drift == "duplicate":
        report["commands"][2] = report["commands"][1]
    elif drift == "migration":
        report["commands"][4]["exitCode"] = 1
    elif drift == "skip":
        path = tmp_path / "scope.xml"
        xml = ET.fromstring(path.read_bytes())
        ET.SubElement(xml.find("testcase"), "skipped")
        path.write_bytes(ET.tostring(xml))
        report["commands"][5]["artifacts"] = [gate.artifact(tmp_path, "scope.xml")]
    elif drift == "artifact":
        (tmp_path / "scope.xml").unlink()
    elif drift == "cleanup":
        report["commands"][6]["cleanup"] = "failed"
    elif drift == "argv":
        report["commands"][5]["argv"] += ["-k", "one"]
    else:
        report["sourceAfter"]["files"][0]["mode"] = 0o755
    with pytest.raises((ValueError, KeyError)):
        gate.validate_report(tmp_path, report)


def test_migration_cannot_omit_a_frozen_business_table(gate):
    value = schema_fixture("0006_validation_identity")
    value["preserved_rows"] = {"chat_turn": 1}
    with pytest.raises(ValueError):
        gate.validate_schema(value, "0006_validation_identity")


def test_storage_cleanup_must_name_the_actual_owned_container_and_volume(gate):
    value = {
        "minio": {
            "container": "a" * 64,
            "volume": "tap-owned-volume",
            "owner": "b" * 32,
            "image": "sha256:" + "c" * 64,
            "state": "complete",
        },
        "azurite": {
            "container": "d" * 64,
            "project": "tap-task5-tests-0123456789ab",
            "owner": "e" * 32,
            "state": "complete",
        },
    }
    gate.validate_storage_ownership(value)
    for provider in value:
        broken = copy.deepcopy(value)
        broken[provider].pop("container")
        with pytest.raises(ValueError):
            gate.validate_storage_ownership(broken)


def test_failed_child_teardown_stops_actual_runner_before_later_resources(
    gate, monkeypatch, tmp_path
):
    import os

    monkeypatch.setattr(gate.os, "environ", os.environ.copy())
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    source = {
        "planningSha": gate.PLANNING_SHA,
        "head": "a" * 40,
        "dirty": True,
        "files": [{"path": "gate.py", "mode": 0o644, "sha256": "b" * 64}],
    }
    monkeypatch.setattr(gate, "source_snapshot", lambda: copy.deepcopy(source))
    registry = gate.command_registry
    monkeypatch.setattr(
        gate,
        "command_registry",
        lambda output: [
            row for row in registry(output) if row["id"] in {"project-audit", "storage"}
        ],
    )

    def failed_child(argv, env, log):
        log.write_text("fixture-owned MySQL teardown failed\n")
        xml = ET.fromstring(
            junit(
                [
                    (
                        "apps.backend.tests.integration.test_validation_identity_registry",
                        "test_fixture",
                        "error",
                    )
                ],
                errors=1,
            )
        )
        ET.SubElement(xml.find("testcase"), "system-err").text = "\n".join(
            json.dumps(
                {"event": "owned-mysql", "identity": "tap-schema-0123456789ab", "state": state}
            )
            for state in ["started", "failed"]
        )
        (log.parent / "project-audit.xml").write_bytes(ET.tostring(xml))
        return 1

    monkeypatch.setattr(gate, "execute", failed_child)
    original = gate.run_command
    visited = []

    def observed(command, output, env):
        visited.append(command["id"])
        if len(visited) > 1:
            raise AssertionError("later resource creation was scheduled")
        return original(command, output, env)

    monkeypatch.setattr(gate, "run_command", observed)
    previous = os.umask(0o077)
    try:
        assert gate.run_gate() == 1
    finally:
        os.umask(previous)
    assert visited == ["project-audit"]
    report = json.loads(next((tmp_path / ".tapper/v0-gate").glob("*/report.json")).read_text())
    assert report["cleanup"] != "complete"
    assert report["commands"][0]["cleanup"] != "complete"
    assert report["commands"][1]["state"] == "not-run"
    assert report["commands"][0]["exitCode"] == 1
    entry = report["commands"][0]["artifacts"][0]
    assert entry["path"] == "project-audit.xml"
    directory = next((tmp_path / ".tapper/v0-gate").iterdir())
    assert gate.read_artifact(directory, entry) == (directory / "project-audit.xml").read_bytes()


@pytest.mark.parametrize("mask", [0o022, 0o077])
def test_probe_child_image_read_mode_is_independent_of_host_umask(monkeypatch, tmp_path, mask):
    import os
    import stat

    from scripts import parser_test_support as support

    class BuildCaptured(Exception):
        pass

    plugin = tmp_path / "compose-plugin"
    plugin.touch()
    captured = {}

    def docker_boundary(argv, **kwargs):
        if "build" in argv:
            context = Path(argv[-1])
            captured["mode"] = stat.S_IMODE((context / "child.py").stat().st_mode)
            captured["private_mode"] = stat.S_IMODE(context.parent.stat().st_mode)
            captured["state"] = context.parent
            raise BuildCaptured
        if argv[-1] == "verify":
            output = "sha256:" + "a" * 64
        elif argv[1:] == ["context", "show"]:
            output = "default"
        elif argv[1:3] == ["context", "inspect"]:
            output = "unix:///local/docker.sock"
        elif argv[1] == "info":
            output = str(plugin)
        else:
            raise AssertionError("unexpected external operation")
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(subprocess, "run", docker_boundary)
    previous = os.umask(mask)
    try:
        with pytest.raises(BuildCaptured):
            with support.isolated_probe():
                raise AssertionError("no image or service should be created")
    finally:
        os.umask(previous)
    assert captured["mode"] == 0o644
    assert captured["private_mode"] == 0o700
    assert not captured["state"].exists()


def test_schema_v2_requires_named_0010_inventory(gate):
    value = schema_fixture()
    value.update(
        tables=25, revision="0010_knowledge_sources", table_names=sorted(gate.SOURCE_SCHEMA_TABLES)
    )
    gate.validate_schema(value, None, schema_version=2)
    for changes in (
        {"revision": "0009_outbox_operations"},
        {"tables": 21},
        {"table_names": []},
        {"table_names": [*value["table_names"][:-1], "unexpected"]},
    ):
        with pytest.raises(ValueError):
            gate.validate_schema({**value, **changes}, None, schema_version=2)


def test_schema_v3_requires_source_command_revision_and_exact_26_tables(gate):
    value = schema_fixture()
    value.update(
        tables=26,
        revision="0010a_source_commands",
        table_names=sorted(gate.SOURCE_SCHEMA_TABLES | {"knowledge_source_command"}),
    )
    gate.validate_schema(value, None, schema_version=3)
    for changed in (
        {"revision": "0010_knowledge_sources"},
        {"tables": 25},
        {"table_names": sorted(gate.SOURCE_SCHEMA_TABLES)},
        {"table_names": [*value["table_names"][:-1], "unexpected"]},
    ):
        with pytest.raises(ValueError):
            gate.validate_schema({**value, **changed}, None, schema_version=3)
    with pytest.raises(ValueError):
        gate.validate_schema(value, None, schema_version=2)
