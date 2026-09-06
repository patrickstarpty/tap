"""Fixed owned V0 gate. Native evidence validators remain inert when imported."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PLANNING_SHA = "a54ab433eae52500683a5ff6ff9d79466a30e1ca"
MAX_ARTIFACT = 16 * 1024 * 1024
BUNDLES = {
    "scope": [
        "contract/test_validation_authorization_policy.py",
        "contract/test_alternate_authorization_policy.py",
        "unit/access/test_scope_context.py",
        "contract/test_validation_scope_http.py",
        "contract/test_origin_policy.py",
    ],
    "project-audit": [
        "integration/test_validation_identity_registry.py",
        "integration/test_document_project_isolation.py",
        "contract/test_project_audit_port.py",
        "integration/test_project_audit_transaction.py",
    ],
    "recovery-operator": [
        "unit/operations/test_redis_stream_recovery.py",
        "unit/operations/test_knowledge_operator.py",
        "integration/test_outbox_archive.py",
        "integration/test_knowledge_operations_recovery.py",
        "integration/test_project_outbox.py",
        "integration/test_relay_recovery.py",
    ],
    "storage": [
        "integration/test_minio_artifacts.py",
        "integration/test_azurite_artifacts.py",
    ],
    "parser-security": [
        "security/test_document_upload_security.py",
        "contract/test_isolated_parser.py",
        "security/test_owned_parser.py",
    ],
}
REVISIONS = [
    "0006_validation_identity",
    "0007_project_scope_backfill",
    "0008_project_audit",
    "0009_outbox_operations",
]
PRESERVED_TABLES = {
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
}

ANCHORS = {
    "contract/test_validation_authorization_policy.py": [
        "test_scope_identity_allows_registered_actor_and_rechecks_disable",
        "test_scope_identity_denies_invalid_authority",
        "test_scope_identity_denial_precedes_retrieval_io",
    ],
    "contract/test_alternate_authorization_policy.py": [
        "test_scope_identity_allows_registered_actor_and_rechecks_disable",
        "test_scope_identity_denies_invalid_authority",
        "test_scope_identity_denial_precedes_retrieval_io",
    ],
    "unit/operations/test_redis_stream_recovery.py": [
        "test_recovery_trim_preserves_pending_and_unread_across_all_groups",
        "test_recovery_real_reclaim_moves_only_expired_pending_hints",
        "test_recovery_publisher_frees_acknowledged_capacity_without_dropping_pending",
    ],
    "integration/test_knowledge_operations_recovery.py": [
        "test_operator_recovery_lease_replay_and_atomic_completion",
        "test_operator_ready_snapshot_returns_populated_work_and_rejects_limit_plus_one",
    ],
    "integration/test_minio_artifacts.py": [
        "test_owned_minio_restart_retains_manifest_digest_and_recovers_staging",
        "test_owned_mixed_azure_recovery_and_minio_artifacts_preserve_legacy_refs",
        "test_native_s3_partial_body_cancel_closes_socket_and_settles",
    ],
    "security/test_owned_parser.py": [
        "test_owned_parser_hostile_matrix_then_valid_recovery",
        "test_owned_parser_resource_faults_reap_whole_container",
        "test_owned_parser_abnormal_exit_retains_state_and_cold_recovery",
        "test_actual_dev_launcher_cold_restart_reconciles_persisted_owner",
    ],
}
PHASES = {
    "journey": [
        "tests/e2e/tapper.spec.ts",
        "tests/e2e/knowledge-upload-security.spec.ts",
    ],
    "app-restart": ["tests/e2e/persistence.spec.ts"],
    "compose-restart": ["tests/e2e/persistence.spec.ts"],
    "verify": ["apps/backend/tests/integration/test_tapper_persistence_restart.py"],
}
E2E_TITLES = {
    "tests/e2e/tapper.spec.ts": "Library uploads/status and Project API recovery, answers, citations, scope, digests and deletion",
    "tests/e2e/knowledge-upload-security.spec.ts": "Library rejects hostile uploads and accepts a subsequent safe document",
    "tests/e2e/persistence.spec.ts": "Tapper durable state survives the selected restart boundary",
    PHASES["verify"][
        0
    ]: "test_exact_tapper_state_survives_application_and_compose_restarts",
}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: Any) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def hash_string(value: Any, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch("[a-f0-9]{" + str(length) + "}", value) is not None
    )


def command_registry(output: Path) -> list[dict[str, Any]]:
    commands = [{"id": "schema", "kind": "schema", "argv": ["make", "schema-drift"]}]
    commands += [
        {
            "id": revision,
            "kind": "schema",
            "argv": ["make", "migration-check", "MIGRATION=" + revision],
        }
        for revision in REVISIONS
    ]
    for name, files in BUNDLES.items():
        commands.append(
            {
                "id": name,
                "kind": "pytest",
                "argv": [
                    "uv",
                    "run",
                    "--project",
                    "apps/backend",
                    "pytest",
                    "-q",
                    *["apps/backend/tests/" + file for file in files],
                    "--junitxml=" + str(output / (name + ".xml")),
                    "-o",
                    "xfail_strict=true",
                    "-o",
                    "junit_logging=all",
                    "-o",
                    "junit_log_passing_tests=true",
                ],
            }
        )
    commands.append({"id": "e2e", "kind": "e2e", "argv": ["make", "demo-e2e"]})
    return commands


def safe_path(root: Path, name: str) -> Path:
    relative = Path(name)
    require(
        bool(name) and not relative.is_absolute() and ".." not in relative.parts,
        "unsafe artifact path",
    )
    root = root.absolute()
    for parent in [*reversed(root.parents), root]:
        require(not parent.is_symlink(), "symlink artifact parent")
    path = root
    for part in relative.parts:
        path = path / part
        require(not path.is_symlink(), "symlink artifact")
    require(path.is_relative_to(root), "artifact escaped root")
    return path


def read_bytes(root: Path, name: str) -> bytes:
    path = safe_path(root, name)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_ARTIFACT,
                "empty or oversized artifact",
            )
            raw = stream.read(MAX_ARTIFACT + 1)
            after = os.fstat(stream.fileno())
        require(
            len(raw) == before.st_size
            and (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ),
            "artifact changed while reading",
        )
        return raw
    except OSError as error:
        raise ValueError("artifact absent or unreadable") from error


def artifact(root: Path, name: str) -> dict[str, Any]:
    raw = read_bytes(root, name)
    return {"path": name, "bytes": len(raw), "sha256": sha(raw)}


def read_artifact(root: Path, entry: dict[str, Any]) -> bytes:
    raw = read_bytes(root, entry["path"])
    require(
        type(entry.get("bytes")) is int
        and entry["bytes"] == len(raw)
        and entry.get("sha256") == sha(raw),
        "artifact digest mismatch",
    )
    return raw


def write_json(root: Path, name: str, value: Any) -> None:
    path = safe_path(root, name)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def ownership_events(text: str) -> list[dict[str, str]]:
    events = []
    for line in text.splitlines():
        if not line.startswith('{"event": "owned-'):
            continue
        value = json.loads(line)
        if value.get("event") in {"owned-mysql", "owned-redis"}:
            require(
                set(value) == {"event", "identity", "state"}, "invalid ownership event"
            )
            events.append(value)
    return events


def validate_ownership(events: list[dict[str, str]], kind: str, minimum: int) -> int:
    states: dict[str, list[str]] = {}
    for event in events:
        if event.get("event") != "owned-" + kind:
            continue
        identity = event.get("identity", "")
        prefix = "tap-schema-" if kind == "mysql" else "tap-recovery-test-"
        require(
            re.fullmatch(prefix + "[a-f0-9]{12}", identity) is not None,
            "invalid owned identity",
        )
        states.setdefault(identity, []).append(event.get("state", ""))
    require(
        len(states) >= minimum
        and all(value == ["started", "complete"] for value in states.values()),
        "owned cleanup incomplete",
    )
    return len(states)


def parse_junit(
    raw: bytes, files: list[str], anchors: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    require(
        0 < len(raw) <= MAX_ARTIFACT
        and b"<!DOCTYPE" not in raw.upper()
        and b"<!ENTITY" not in raw.upper(),
        "invalid native XML",
    )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError("truncated native XML") from error
    require(root.tag in {"testsuites", "testsuite"}, "invalid JUnit root")
    suites = list(root) if root.tag == "testsuites" else [root]
    cases: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    events: list[dict[str, str]] = []
    modules = {}
    for file in files:
        module = file[:-3].replace("/", ".")
        for prefix in ["apps.backend.tests.", "tests."]:
            classname = prefix + module
            if file == "contract/test_validation_authorization_policy.py":
                classname += ".TestValidationIdentityPolicy"
            if file == "contract/test_alternate_authorization_policy.py":
                classname += ".TestAlternateIdentityPolicy"
            modules[classname] = file
    for suite in suites:
        require(suite.tag == "testsuite", "invalid native suite")
        require(
            all(suite.get(key) == "0" for key in ("errors", "failures", "skipped")),
            "native failure or skip",
        )
        native_cases = suite.findall("testcase")
        require(
            suite.get("tests", "").isdecimal()
            and int(suite.attrib["tests"]) == len(native_cases),
            "native count mismatch",
        )
        for case in native_cases:
            classname, name = case.get("classname", ""), case.get("name", "")
            require(
                classname in modules
                and 0 < len(name) <= 1024
                and all(ord(c) >= 32 for c in name),
                "unexpected native test identity",
            )
            require((classname, name) not in seen, "duplicate native testcase")
            seen.add((classname, name))
            require(
                all(
                    child.tag in {"properties", "system-out", "system-err"}
                    for child in case
                ),
                "native skipped, failed or retried",
            )
            cases.append(
                {"file": modules[classname], "classname": classname, "name": name}
            )
        for node in suite.iter("system-err"):
            events.extend(ownership_events(node.text or ""))
    require(
        bool(cases) and {case["file"] for case in cases} == set(files),
        "required domain missing",
    )
    for file, required_names in (anchors or {}).items():
        if file in files:
            actual = {
                case["name"].split("[", 1)[0] for case in cases if case["file"] == file
            }
            require(set(required_names) <= actual, "required native behavior missing")
    return {
        "counts": {"passed": len(cases), "skipped": 0, "failed": 0},
        "cases": cases,
        "ownership": events,
    }


def validate_e2e(value: dict[str, Any], phase: str) -> dict[str, int]:
    require(
        value.get("schemaVersion") == 1
        and value.get("phase") == phase
        and value.get("cleanup") == "complete"
        and hash_string(value.get("nativeSha256")),
        "invalid E2E provenance or cleanup",
    )
    files: dict[str, set[str]] = {}
    identities = set()
    count = 0
    for row in value.get("specifications", []):
        file, title = row.get("file"), row.get("title")
        require(
            file in PHASES[phase] and isinstance(title, str) and bool(title),
            "unexpected E2E specification",
        )
        require((file, title) not in identities, "duplicate E2E specification")
        identities.add((file, title))
        files.setdefault(file, set()).add(title)
        tests = row.get("tests", [])
        require(bool(tests), "empty E2E test")
        for test in tests:
            require(
                test
                == {
                    "project": "pytest" if phase == "verify" else "chromium",
                    "expectedStatus": "passed",
                    "status": "expected",
                    "results": [{"status": "passed", "retry": 0}],
                },
                "E2E skipped, failed or retried",
            )
            count += 1
    require(
        set(files) == set(PHASES[phase])
        and all(E2E_TITLES[file] in titles for file, titles in files.items()),
        "required E2E phase behavior absent",
    )
    expected = {"passed": count, "failed": 0, "flaky": 0, "skipped": 0}
    require(
        count > 0
        and value.get("counts") == expected
        and all(type(v) is int for v in value["counts"].values()),
        "E2E native count mismatch",
    )
    return expected


def validate_schema(value: dict[str, Any], revision: str | None) -> None:
    require(value.get("status") == "passed", "schema or migration failed")
    owned = value.get("ownership", {})
    require(
        owned.get("event") == "owned-mysql"
        and owned.get("state") == "complete"
        and re.fullmatch(r"tap-schema-[a-f0-9]{12}", owned.get("identity", ""))
        is not None,
        "schema cleanup missing",
    )
    if revision is None:
        require(
            value.get("gate") == "schema-drift"
            and value.get("tables") == 21
            and value.get("differences") == [],
            "schema drift evidence incomplete",
        )
        return
    require(
        value.get("gate") == "migration-check" and value.get("revision") == revision,
        "wrong migration identity",
    )
    rows = value.get("preserved_rows", {})
    require(
        isinstance(rows, dict)
        and set(rows) == PRESERVED_TABLES
        and all(type(v) is int and v > 0 for v in rows.values()),
        "frozen baseline preservation missing",
    )
    require(
        value.get("identity_seed")
        == {
            "enterprise": "local",
            "project": "tapper-demo",
            "actor": "tapper-local-user",
            "principal_type": "VALIDATION",
        },
        "identity seed missing",
    )
    fields = ["downgrade_replay"]
    fields += {
        REVISIONS[0]: [],
        REVISIONS[1]: ["scope_backfill", "constraints", "pre_ddl_rejection"],
        REVISIONS[2]: ["scope_backfill", "audit_constraints", "audit_downgrade_replay"],
        REVISIONS[3]: ["operations_constraints", "operations_downgrade_replay"],
    }[revision]
    require(
        all(value.get(field) == "passed" for field in fields),
        "migration replay or constraints missing",
    )


def validate_storage_ownership(value: dict[str, Any]) -> None:
    require(set(value) == {"minio", "azurite"}, "storage ownership absent")
    for provider, record in value.items():
        require(
            hash_string(record.get("container"))
            and hash_string(record.get("owner"), 32)
            and record.get("state") == "complete",
            "storage resource cleanup missing",
        )
        if provider == "minio":
            require(
                isinstance(record.get("volume"), str)
                and bool(record["volume"])
                and isinstance(record.get("image"), str)
                and record["image"].startswith("sha256:")
                and hash_string(record["image"][7:]),
                "MinIO resource identity missing",
            )
        else:
            require(
                re.fullmatch(r"tap-task5-tests-[a-f0-9]{12}", record.get("project", ""))
                is not None,
                "Azurite owned project missing",
            )


def validate_provenance(before: dict[str, Any], after: dict[str, Any]) -> None:
    require(
        before.get("planningSha") == PLANNING_SHA
        and hash_string(before.get("head"), 40)
        and type(before.get("dirty")) is bool,
        "invalid planning/source provenance",
    )
    files = before.get("files", [])
    require(bool(files), "source manifest empty")
    paths = []
    for row in files:
        require(
            isinstance(row.get("path"), str)
            and not Path(row["path"]).is_absolute()
            and ".." not in Path(row["path"]).parts
            and hash_string(row.get("sha256"))
            and type(row.get("mode")) is int,
            "invalid source manifest",
        )
        paths.append(row["path"])
    require(
        len(set(paths)) == len(paths) and before == after, "source changed during gate"
    )


def source_snapshot() -> dict[str, Any]:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=ROOT)

    require(
        git("rev-parse", PLANNING_SHA + "^{commit}").decode().strip() == PLANNING_SHA,
        "planning commit unavailable",
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", PLANNING_SHA, "HEAD"],
        cwd=ROOT,
        check=True,
    )
    paths = sorted(
        set(
            git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
            .decode()
            .split("\0")
        )
        - {""}
    )
    records = []
    excluded = {
        "node_modules",
        ".tapper",
        ".superpowers",
        ".venv",
        "test-results",
        "dist",
        "__pycache__",
    }
    for name in paths:
        path = Path(name)
        if (
            excluded.intersection(path.parts)
            or path.suffix == ".md"
            or path.parts[0] in {"docs", ".agents", ".codex"}
        ):
            continue
        if not (
            path.parts[0] in {"apps", "scripts", "deploy", "contracts"}
            or len(path.parts) == 1
        ):
            continue
        actual = ROOT / path
        require(not actual.is_symlink(), "unexpected source symlink")
        metadata = actual.stat()
        require(stat.S_ISREG(metadata.st_mode), "invalid source file")
        records.append(
            {
                "path": name,
                "mode": stat.S_IMODE(metadata.st_mode),
                "sha256": sha(actual.read_bytes()),
            }
        )
    dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
    return {
        "planningSha": PLANNING_SHA,
        "head": git("rev-parse", "HEAD").decode().strip(),
        "dirty": dirty,
        "files": records,
    }


def validate_report(output: Path, report: dict[str, Any]) -> dict[str, int]:
    require(
        report.get("schemaVersion") == 1 and report.get("cleanup") == "complete",
        "report or cleanup incomplete",
    )
    validate_provenance(report["sourceBefore"], report["sourceAfter"])
    registry = command_registry(output)
    require(
        report.get("registrySha256") == digest(registry), "command registry changed"
    )
    commands = report.get("commands", [])
    require(len(commands) == len(registry), "required command missing")
    totals = {"passed": 0, "skipped": 0, "failed": 0}
    for command, expected in zip(commands, registry, strict=True):
        require(
            all(command.get(key) == value for key, value in expected.items())
            and command.get("cwd") == str(ROOT)
            and type(command.get("exitCode")) is int
            and command["exitCode"] == 0,
            "required command failed or identity changed",
        )
        require(command.get("cleanup") == "complete", "command cleanup incomplete")
        require(
            command.get("log", {}).get("path") == expected["id"] + ".log",
            "wrong command log",
        )
        read_artifact(output, command["log"])
        artifacts = command.get("artifacts", [])
        name = expected["id"]
        if expected["kind"] == "schema":
            require(
                [a["path"] for a in artifacts] == [name + ".json"],
                "schema artifact absent",
            )
            value = json.loads(read_artifact(output, artifacts[0]))
            validate_schema(value, None if name == "schema" else name)
            validate_ownership(command.get("ownership", []), "mysql", 1)
        elif expected["kind"] == "pytest":
            require(
                [a["path"] for a in artifacts] == [name + ".xml"], "native JUnit absent"
            )
            parsed = parse_junit(
                read_artifact(output, artifacts[0]), BUNDLES[name], ANCHORS
            )
            if name in {"project-audit", "recovery-operator"}:
                validate_ownership(parsed["ownership"], "mysql", 1)
            if name == "recovery-operator":
                validate_ownership(parsed["ownership"], "redis", 3)
                validate_ownership(command.get("ownership", []), "mysql", 1)
            if name == "storage":
                validate_storage_ownership(command.get("storageOwnership", {}))
                require(
                    command.get("storageCleanup")
                    == {"minio": "complete", "azurite": "complete"},
                    "storage cleanup missing",
                )
            totals["passed"] += parsed["counts"]["passed"]
        else:
            require(
                [a["path"] for a in artifacts]
                == ["e2e/phase-" + phase + ".json" for phase in PHASES],
                "E2E phase absent",
            )
            for phase, entry in zip(PHASES, artifacts, strict=True):
                totals["passed"] += validate_e2e(
                    json.loads(read_artifact(output, entry)), phase
                )["passed"]
    return totals


def clean_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "LANG",
        "LC_ALL",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(
        UV_NO_SYNC="1",
        PYTHONPATH=str(ROOT / "apps/backend/src") + os.pathsep + str(ROOT),
    )
    return env


def execute(argv: list[str], env: dict[str, str], log: Path) -> int:
    descriptor = os.open(
        log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=3600)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=180)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "owned command cleanup unresolved; retain run evidence"
                ) from error
            raise


@contextlib.contextmanager
def owned_azurite(
    env: dict[str, str], result: dict[str, str], identities: dict[str, Any]
) -> Iterator[tuple[Path, str]]:
    from scripts.azurite_test_support import (
        IMAGE,
        PURPOSE,
        require_owned_azurite,
        write_owned_azurite_receipt,
    )
    from scripts.migration_support import _run

    context = _run(["docker", "context", "show"], env=env)
    endpoint = _run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ],
        env=env,
    )
    require(endpoint.startswith("unix://"), "Azurite requires local Docker")
    project = "tap-task5-tests-" + uuid.uuid4().hex[:12]
    owner = uuid.uuid4().hex
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix=project + "-") as directory:
        path = Path(directory)
        recipe = path / "compose.json"
        recipe.write_text(
            json.dumps(
                {
                    "services": {
                        "azurite": {
                            "image": IMAGE,
                            "labels": {
                                "io.tap.test.owner": owner,
                                "io.tap.test.purpose": PURPOSE,
                            },
                            "ports": [f"127.0.0.1:{port}:{port}"],
                            "tmpfs": ["/data"],
                            "command": [
                                "azurite-blob",
                                "--blobHost",
                                "0.0.0.0",
                                "--blobPort",
                                str(port),
                                "--silent",
                                "--location",
                                "/data",
                            ],
                        }
                    }
                }
            )
        )
        docker = [
            "docker",
            "--context",
            context,
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(recipe),
            "-p",
            project,
        ]
        result["azurite"] = "pending"
        try:
            _run([*docker, "up", "-d"], env=env)
            container = _run([*docker, "ps", "-q", "azurite"], env=env)
            receipt = write_owned_azurite_receipt(
                path / "receipt.json",
                project=project,
                owner=owner,
                container_id=container,
                docker_context=context,
            )
            identities["azurite"] = {
                "container": container,
                "project": project,
                "owner": owner,
                "state": "pending",
            }
            owned = require_owned_azurite(receipt)
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("owned Azurite not ready")
            yield receipt, owned.connection_string
        finally:
            try:
                _run([*docker, "down", "--volumes", "--remove-orphans"], env=env)
                result["azurite"] = "complete"
                if "azurite" in identities:
                    identities["azurite"]["state"] = "complete"
            except BaseException:
                result["azurite"] = "failed"
                if "azurite" in identities:
                    identities["azurite"]["state"] = "failed"
                raise


def run_command(
    command: dict[str, Any], output: Path, env: dict[str, str]
) -> dict[str, Any]:
    from scripts.migration_support import isolated_mysql
    from scripts.minio_test_support import isolated_minio

    row = {
        **command,
        "cwd": str(ROOT),
        "exitCode": None,
        "artifacts": [],
        "cleanup": "pending",
        "started": time.time(),
    }
    name = command["id"]
    capture = io.StringIO()
    storage: dict[str, str] = {}
    identities: dict[str, Any] = {}
    print("V0 gate starting " + name, flush=True)
    try:
        with (
            contextlib.redirect_stderr(capture),
            contextlib.redirect_stdout(capture),
            contextlib.ExitStack() as stack,
        ):
            child_env = dict(env)
            if name in {"project-audit", "recovery-operator"}:
                child_env["TAP_RUN_MYSQL_INTEGRATION"] = "1"
            if name == "recovery-operator":
                database = stack.enter_context(isolated_mysql())
                database.upgrade("head")
                child_env["TAP_DATABASE_URL"] = database.url.replace(
                    "mysql+pymysql:", "mysql+asyncmy:", 1
                )
                child_env["TAP_RUN_REDIS_INTEGRATION"] = "1"
            if name == "storage":
                owned = stack.enter_context(isolated_minio())
                identities["minio"] = {
                    "container": owned.container_id,
                    "volume": owned.volume,
                    "owner": owned.owner,
                    "image": owned.image_id,
                    "state": "pending",
                }
                receipt, connection = stack.enter_context(
                    owned_azurite(env, storage, identities)
                )
                child_env.update(
                    TAP_RUN_MINIO_INTEGRATION="1",
                    TAP_TEST_MINIO_RECEIPT=str(owned.receipt_path),
                    TAP_RUN_AZURITE_INTEGRATION="1",
                    TAP_TEST_AZURITE_RECEIPT=str(receipt),
                    AZURITE_CONNECTION_STRING=connection,
                )
            if name == "parser-security":
                child_env["TAP_RUN_PARSER_SECURITY"] = "1"
            if name == "e2e":
                safe_path(output, "e2e").mkdir(mode=0o700)
                child_env["TAPPER_E2E_EVIDENCE_DIR"] = str(output / "e2e")
            row["exitCode"] = execute(
                command["argv"], child_env, safe_path(output, name + ".log")
            )
        # Outer context exit cannot prove a failed child's nested fixture cleanup.
        # Preserve its native artifacts, but stop before creating later resources.
        row["cleanup"] = "complete" if row["exitCode"] == 0 else "unresolved"
        if row["exitCode"] != 0:
            row["cleanupReason"] = "failed-child-cleanup-unverified"
    except BaseException as error:
        row["error"] = type(error).__name__
        row["cleanup"] = "failed"
    row["finished"] = time.time()
    text = capture.getvalue()
    row["ownership"] = ownership_events(text)
    if name == "storage":
        minio = [
            json.loads(line)
            for line in text.splitlines()
            if line.startswith("{") and '"owned-minio-cleanup"' in line
        ]
        storage["minio"] = (
            "complete"
            if len(minio) == 1
            and minio[0].get("container_removed") is True
            and minio[0].get("volume_removed") is True
            else "failed"
        )
        row["storageCleanup"] = storage
        if "minio" in identities:
            identities["minio"]["state"] = storage["minio"]
        row["storageOwnership"] = identities
    try:
        log = read_bytes(output, name + ".log")
        row["log"] = artifact(output, name + ".log")
        if command["kind"] == "schema":
            row["ownership"] += ownership_events(log.decode(errors="replace"))
            records = [
                json.loads(line)
                for line in log.decode().splitlines()
                if line.startswith("{") and '"gate"' in line
            ]
            require(len(records) == 1, "schema native JSON missing")
            write_json(output, name + ".json", records[0])
            row["artifacts"] = [artifact(output, name + ".json")]
        elif command["kind"] == "pytest":
            row["artifacts"] = [artifact(output, name + ".xml")]
            parsed = parse_junit(
                read_bytes(output, name + ".xml"), BUNDLES[name], ANCHORS
            )
            row["counts"] = parsed["counts"]
            row["nativeOwnership"] = parsed["ownership"]
        else:
            row["absentPhases"] = []
            for phase in PHASES:
                try:
                    row["artifacts"].append(
                        artifact(output, "e2e/phase-" + phase + ".json")
                    )
                except ValueError:
                    row["absentPhases"].append(phase)
    except (ValueError, OSError, KeyError) as error:
        row["evidenceError"] = type(error).__name__
    print("V0 gate finished " + name + ": exit=" + str(row["exitCode"]), flush=True)
    return row


def run_gate() -> int:
    os.umask(0o077)
    env = clean_environment()
    os.environ.clear()
    os.environ.update(env)
    base = safe_path(ROOT, ".tapper/v0-gate")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = base / uuid.uuid4().hex
    output.mkdir(mode=0o700)
    print("V0 gate evidence: " + str(output), flush=True)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "commands": [],
        "cleanup": "pending",
        "verdict": "fail",
    }
    try:
        report["sourceBefore"] = source_snapshot()
        registry = command_registry(output)
        report["registrySha256"] = digest(registry)
        for command in registry:
            report["commands"].append(run_command(command, output, env))
            if report["commands"][-1]["cleanup"] != "complete":
                # Never continue resource creation after unresolved cleanup.
                for pending in registry[len(report["commands"]) :]:
                    report["commands"].append(
                        {
                            **pending,
                            "cwd": str(ROOT),
                            "exitCode": None,
                            "state": "not-run",
                            "cleanup": "not-started",
                            "artifacts": [],
                        }
                    )
                break
        report["cleanup"] = (
            "complete"
            if all(row["cleanup"] == "complete" for row in report["commands"])
            else "failed"
        )
        report["sourceAfter"] = source_snapshot()
        report["counts"] = validate_report(output, report)
        report["verdict"] = "pass"
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__
        if isinstance(error, ValueError):
            report["reason"] = str(error)
    write_json(output, "report.json", report)
    print(
        "V0 gate " + report["verdict"] + ": " + str(output / "report.json"), flush=True
    )
    return 0 if report["verdict"] == "pass" else 1


def main() -> int:
    try:
        if sys.argv[1:] == ["run"]:
            return run_gate()
        if len(sys.argv) == 3 and sys.argv[1] == "validate":
            path = Path(sys.argv[2]).absolute()
            validate_report(path.parent, json.loads(read_bytes(path.parent, path.name)))
            print("pass")
            return 0
    except (Exception, KeyboardInterrupt):
        pass
    print("fail: invalid V0 gate evidence or invocation")
    return 1


if __name__ == "__main__":
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
