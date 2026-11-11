"""Opt-in generated LCA replay in real isolated Chromium, no model calls."""

import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from tap_platform.browser_automation.contracts import (
    BrowserBundle,
    BrowserLimits,
    Evidence,
)
from tap_platform.browser_automation.docker import (
    BrowserRuntime,
    SubprocessDockerClient,
)
from tap_platform.lca.definition import validate_definition
from tap_platform.lca.generator import generate_playwright


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("TAP_RUN_LCA_BROWSER") != "1",
    reason="owned Docker browser opt-in required",
)
async def test_generated_flow_pass_and_failed_assertion_have_real_evidence(tmp_path):
    failures = []

    class DiagnosticClient(SubprocessDockerClient):
        async def run(self, argv, stdin=None, *, max_stdout=1_000_000):
            result = await super().run(argv, stdin, max_stdout=max_stdout)
            if result.exit_code:
                failures.append((argv[:3], result.stderr[-2000:]))
            return result

    class DiagnosticRuntime(BrowserRuntime):
        async def _execute_work(self, *args, **kwargs):
            try:
                return await super()._execute_work(*args, **kwargs)
            except Exception as exc:
                failures.append(("runtime", repr(exc)))
                raise

    client = DiagnosticClient()
    image = os.getenv("TAP_LCA_BROWSER_IMAGE", "tap-lca-browser:20261110-develop")
    site = f"lca-owned-{uuid.uuid4().hex}"
    fixture = Path(__file__).resolve().parents[4] / "scripts/browser_core_site.mjs"
    started = await client.run(
        (
            "docker",
            "run",
            "-d",
            "--name",
            site,
            "--label",
            "tap.lca.test=owned",
            "--network",
            "bridge",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--read-only",
            "--mount",
            f"type=bind,source={fixture},target=/opt/browser/lca-site.mjs,readonly",
            image,
            "node",
            "/opt/browser/lca-site.mjs",
        )
    )
    assert started.exit_code == 0, started.stderr

    class Sink:
        def persist(self, project_id, task_id, execution_id, kind, data, max_bytes):
            assert len(data) <= max_bytes
            sha = hashlib.sha256(data).hexdigest()
            (tmp_path / sha).write_bytes(data)
            return Evidence(kind, sha, sha, len(data))

    try:
        inspected = await client.run(("docker", "inspect", site))
        ip = json.loads(inspected.stdout)[0]["NetworkSettings"]["Networks"]["bridge"][
            "IPAddress"
        ]
        runtime = DiagnosticRuntime(
            client,
            image,
            evidence_sink=Sink(),
            project_id="lca-test",
            test_target=f"http://{ip}:8080",
        )
        for expected, wanted_status in [
            ("AC-42 / 120 / MATCH", "passed"),
            ("deliberately incorrect", "failed"),
        ]:
            definition = validate_definition(
                {
                    "schema_version": "lca-debug-v1",
                    "allowed_origins": ["http://example.test"],
                    "targets": [
                        {
                            "id": "account",
                            "kind": "css",
                            "value": "[name=account]",
                            "review_required": True,
                        },
                        {
                            "id": "amount",
                            "kind": "css",
                            "value": "[name=amount]",
                            "review_required": True,
                        },
                        {
                            "id": "submit",
                            "kind": "role",
                            "value": "button",
                            "name": "Check request",
                        },
                        {
                            "id": "result",
                            "kind": "css",
                            "value": "#result",
                            "review_required": True,
                        },
                    ],
                    "parameters": [],
                    "steps": [
                        {
                            "id": "open",
                            "operation": "navigate",
                            "arguments": {"url": "http://example.test/review"},
                            "timeout_ms": 5000,
                        },
                        {
                            "id": "account",
                            "operation": "fill",
                            "target_id": "account",
                            "arguments": {"value": "AC-42"},
                            "timeout_ms": 5000,
                        },
                        {
                            "id": "amount",
                            "operation": "fill",
                            "target_id": "amount",
                            "arguments": {"value": "120"},
                            "timeout_ms": 5000,
                        },
                        {
                            "id": "submit",
                            "operation": "click",
                            "target_id": "submit",
                            "arguments": {},
                            "timeout_ms": 5000,
                        },
                        {
                            "id": "verify",
                            "operation": "assert_text",
                            "target_id": "result",
                            "arguments": {"text": expected},
                            "timeout_ms": 1000,
                        },
                    ],
                }
            )
            generated = generate_playwright(definition)
            source = generated.source.encode()
            bundle = BrowserBundle(
                uuid.uuid4().hex,
                "lca-test",
                hashlib.sha256(source).hexdigest(),
                source,
                generated.input_digest,
                ("http://example.test",),
                "2026-11-10T00:00:00Z",
            )
            receipt = await runtime.execute(
                bundle,
                {"task_id": "lca-owned-replay", "input_data": {}},
                BrowserLimits(deadline_seconds=60),
            )
            assert receipt.status == wanted_status, json.dumps(
                {
                    "status": receipt.status,
                    "failure_code": receipt.failure_code,
                    "docker_failures": failures,
                }
            )
            assert {e.kind for e in receipt.evidence} >= {
                "result",
                "screenshot",
                "trace",
            }
            result = json.loads(
                next(
                    (tmp_path / e.object_ref).read_bytes()
                    for e in receipt.evidence
                    if e.kind == "result"
                )
            )
            outputs = result["outputs"]
            canonical = next(
                o for o in outputs if o.get("schema_version") == "lca-debug-result-v1"
            )
            assert canonical["definition_digest"] == generated.definition_digest
            assert canonical["steps"][-1] == {
                "step_id": "verify",
                "status": wanted_status,
            }
    finally:
        await client.run(("docker", "rm", "-f", site))
