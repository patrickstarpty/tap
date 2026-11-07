"""Real MySQL Chat browser admission and public project-scoped task reads."""

import asyncio
import base64
import hashlib
import json
import os
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import (
    VALIDATION_SCOPE,
    ValidationAuthorizationPolicy,
    ValidationScopeProvider,
)
from tap.modules.access.domain.authorization import ActorPrincipal
from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
from tap.modules.ai.application.assets import workflow_asset_seed
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.adapters.mysql_workflow_bridge import MysqlWorkflowChatBridge
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import FrozenBrowserIntent, TurnInput
from tap.modules.workflows.adapters.asset_catalog import AssetWorkflowCatalog
from tap.modules.workflows.adapters.browser_client import BrowserApiClient
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.schema import workflow_event, workflow_task
from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
from tap.platform.db.project_scope import scope_values
from tests.owned_mysql import owned_project_database_url


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _SavedBrowserClient(BrowserApiClient):
    def __init__(self, project_id: str, receipt: Path, screenshot: Path) -> None:
        super().__init__("http://127.0.0.1:8002", project_id, "x" * 16)
        self._receipt = receipt
        self._screenshot = screenshot

    async def request(self, method: str, path: str, **_kwargs):
        assert (method, path) == ("GET", "/executions/execution-entry")
        return json.loads(self._receipt.read_text(encoding="utf-8"))

    async def read_evidence(self, evidence_id: str):
        assert evidence_id == "evidence-entry"
        return self._screenshot.read_bytes(), "image/png"


async def _verify_browser_ui(
    app, task_id: str, replay_id: str, tmp_path: Path, api_port: int, web_port: int
) -> None:
    root = Path(__file__).resolve().parents[4]
    frontend = root / "apps/tap-ai-frontend"
    capture_dir = frontend / "test-results/task4-browser-entry"
    capture_dir.mkdir(parents=True, exist_ok=True)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="error", lifespan="off")
    )
    server_task = asyncio.create_task(server.serve())
    selected_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TAPPER_API_HOST": "127.0.0.1",
        "TAPPER_API_PORT": str(api_port),
        "TAPPER_WEB_HOST": "127.0.0.1",
        "TAPPER_WEB_PORT": str(web_port),
        "TAPPER_WEB_BASE_URL": f"http://127.0.0.1:{web_port}",
        "TAP_RUN_BROWSER_ENTRY_UI": "1",
        "TAPPER_BROWSER_ENTRY_TASK_ID": task_id,
        "TAPPER_BROWSER_ENTRY_REPLAY_ID": replay_id,
        "TAPPER_BROWSER_ENTRY_CAPTURE_DIR": str(capture_dir),
    }
    log_path = tmp_path / "vite.log"
    with log_path.open("wb") as log:
        vite = subprocess.Popen(
            [
                str(frontend / "node_modules/.bin/vite"),
                str(frontend),
                "--config",
                str(frontend / "vite.config.ts"),
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
                "--strictPort",
            ],
            cwd=root,
            env=selected_env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                for _ in range(120):
                    if vite.poll() is not None or server_task.done():
                        raise AssertionError("browser entry UI servers exited early")
                    try:
                        api = await client.get(f"http://127.0.0.1:{api_port}/api/v1/runtime-mode")
                        web = await client.get(f"http://127.0.0.1:{web_port}/")
                        if api.status_code == 200 and web.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.25)
                else:
                    raise AssertionError(f"browser entry UI did not become ready: {log_path}")
            browser = await asyncio.create_subprocess_exec(
                "corepack",
                "pnpm",
                "--filter",
                "@tap/ai-frontend",
                "exec",
                "playwright",
                "test",
                "tests/e2e/browser-entry-real-api.spec.ts",
                "--config=playwright.config.ts",
                "--reporter=line",
                "--workers=1",
                cwd=root,
                env=selected_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(browser.communicate(), 150)
            assert browser.returncode == 0, output.decode(errors="replace")[-6000:]
        finally:
            vite.terminate()
            try:
                await asyncio.to_thread(vite.wait, timeout=10)
            except subprocess.TimeoutExpired:
                vite.kill()
                await asyncio.to_thread(vite.wait)
            server.should_exit = True
            await asyncio.wait_for(server_task, 10)


@pytest.mark.asyncio
async def test_browser_chat_turn_is_frozen_linked_and_read_from_real_mysql(
    owned_project_mysql,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        await catalog.seed(workflow_asset_seed(VALIDATION_SCOPE))
        agents = await AssetWorkflowCatalog(catalog).freeze_agents(VALIDATION_SCOPE, ("sdet",))
        data = {"notes": "Use the selected customer Ada"}
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        browser = FrozenBrowserIntent(
            start_url="https://example.org/quotes",
            expected_result="Find Ada's quote",
            allowed_origins=("https://example.org",),
            input_data_json=canonical,
            input_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        )
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        store = MysqlWorkflowStore(sessions)
        admission = WorkflowAdmissionCoordinator(
            conversations,
            MysqlWorkflowChatBridge(sessions, scope=VALIDATION_SCOPE),
            store,
            max_cost_micros=100_000,
        )
        await admission.create(
            "browser-conversation",
            "browser-turn",
            "browser-key",
            TurnInput(
                "Find Ada's quote",
                VALIDATION_SCOPE.actor_id,
                "validation",
                "qwen-plus",
                workflow_root=agents.root,
                workflow_agents=agents.candidates,
                delegate_ids=agents.requested_ids,
                browser=browser,
            ),
        )
        saved = await conversations.load("browser-conversation")
        turn = saved.turns[0]
        assert turn.input_snapshot.value.browser == browser
        assert turn.workflow_task_id is not None
        task = await store.snapshot(VALIDATION_SCOPE, turn.workflow_task_id)
        assert task["request_json"]["kind"] == "browser"
        assert task["request_json"]["browser"]["input_sha256"] == browser.input_sha256
        assert task["request_json"]["budget_limit"]["cost_micros"] == 100_000
        payload = {
            "bundle_ref": "bundle-entry",
            "execution_ref": "execution-entry",
            "status": "passed",
            "replay_model_calls": 0,
        }
        async with sessions.begin() as session:
            await session.execute(
                workflow_event.insert().values(
                    event_id="browser-entry-result",
                    task_id=turn.workflow_task_id,
                    **scope_values(VALIDATION_SCOPE),
                    sequence=2,
                    node_id=None,
                    attempt=1,
                    kind="browser.coding_result",
                    payload=payload,
                    payload_digest="sha256:"
                    + hashlib.sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    occurred_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == turn.workflow_task_id)
                .values(status="succeeded", next_sequence=3)
            )
        receipt_file = tmp_path / "receipt.json"
        receipt_file.write_text(
            json.dumps(
                {
                    "project_id": VALIDATION_SCOPE.project_id,
                    "task_id": turn.workflow_task_id,
                    "execution_id": "execution-entry",
                    "bundle_id": "bundle-entry",
                    "input_sha256": browser.input_sha256,
                    "status": "passed",
                    "failure_code": None,
                    "steps": [{"title": "Read quote", "status": "passed", "duration": 21}],
                    "result": {"outputs": [{"customer": "Ada", "quote": "FOUND"}]},
                    "evidence": [
                        {"kind": "screenshot", "object_ref": "evidence-entry", "sha256": "b" * 64}
                    ],
                }
            ),
            encoding="utf-8",
        )
        screenshot_file = tmp_path / "screenshot.png"
        screenshot_file.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlOH5kAAAAASUVORK5CYII="
            )
        )

        class Registry:
            async def get_principal(self, enterprise_id, project_id, actor_id):
                assert (enterprise_id, project_id, actor_id) == (
                    VALIDATION_SCOPE.enterprise_id,
                    VALIDATION_SCOPE.project_id,
                    VALIDATION_SCOPE.actor_id,
                )
                return ActorPrincipal(
                    enterprise_id=enterprise_id,
                    actor_id=actor_id,
                    principal_type="VALIDATION",
                    enabled=True,
                )

        ui_ports = (
            (_free_port(), _free_port()) if os.getenv("TAP_RUN_BROWSER_ENTRY_UI") == "1" else None
        )
        app = create_app(
            HttpServices(
                scope=VALIDATION_SCOPE,
                scope_provider=ValidationScopeProvider(),
                authorization_policy=ValidationAuthorizationPolicy(Registry()),
                asset_catalog=catalog,
                workflow_store=store,
                workflow_enabled=True,
                browser_client=_SavedBrowserClient(
                    VALIDATION_SCOPE.project_id, receipt_file, screenshot_file
                ),
                browser_enabled=True,
                browser_model_alias="qwen-plus",
            ),
            validation_mode=True,
            allowed_origins=(frozenset({f"http://127.0.0.1:{ui_ports[1] if ui_ports else 15173}"})),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            mode = await client.get("/api/v1/runtime-mode")
            assert mode.status_code == 200
            assert mode.json()["browserEnabled"] is True
            response = await client.get(
                f"/api/v1/projects/{VALIDATION_SCOPE.project_id}/workflows/{turn.workflow_task_id}"
            )
            assert response.status_code == 200, response.text
            assert response.json()["browserIntent"]["startUrl"] == browser.start_url
            assert response.json()["taskKind"] == "browser"
            assert response.json()["browserRun"]["outputs"] == [
                {"customer": "Ada", "quote": "FOUND"}
            ]
            evidence = await client.get(response.json()["browserRun"]["evidence"][0]["url"])
            assert evidence.status_code == 200
            assert evidence.content == screenshot_file.read_bytes()
            replay = await client.post(
                f"/api/v1/projects/{VALIDATION_SCOPE.project_id}/workflows/"
                f"{turn.workflow_task_id}/replay",
                headers={
                    "Idempotency-Key": "browser-entry-replay",
                    "Origin": f"http://127.0.0.1:{ui_ports[1] if ui_ports else 15173}",
                },
            )
            assert replay.status_code == 202, replay.text
            replay_id = replay.json()["taskId"]
            assert replay_id != turn.workflow_task_id
            assert replay.json()["replayOfTaskId"] == turn.workflow_task_id
            assert replay.json()["browserRun"] is None
            assert (await store.task_request(VALIDATION_SCOPE, replay_id))["browser"][
                "input_sha256"
            ] == browser.input_sha256
            listing = await client.get(f"/api/v1/projects/{VALIDATION_SCOPE.project_id}/workflows")
            assert listing.status_code == 200
            assert any(
                item["taskId"] == replay_id and item["replayOfTaskId"] == turn.workflow_task_id
                for item in listing.json()["items"]
            )
        if ui_ports is not None:
            await _verify_browser_ui(app, turn.workflow_task_id, replay_id, tmp_path, *ui_ports)
    finally:
        await engine.dispose()
