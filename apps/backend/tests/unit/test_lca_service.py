"""Real SQLite/browser-object integration of the explicit LCA run boundary."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.objects import BrowserObjectStore
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import (
    BrowserStore,
    metadata as browser_metadata,
)
from tap_platform.lca.service import LcaService
from tap_platform.lca.store import LcaConflict, LcaNotFound, LcaStore, metadata


def actor(**changes):
    return replace(
        AccessPrincipal(
            project_id="project-a",
            principal_id="alice",
            principal_type="user",
            audience="tap-lca",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            actions=frozenset(
                {
                    "lca.flows.create",
                    "lca.flows.read",
                    "lca.drafts.save",
                    "lca.drafts.generate",
                    "lca.runs.create",
                    "lca.runs.read",
                }
            ),
            enabled=True,
        ),
        **changes,
    )


def definition():
    return {
        "schema_version": "lca-debug-v1",
        "allowed_origins": ["https://example.com"],
        "targets": [],
        "parameters": [{"name": "url", "type": "string", "required": True}],
        "steps": [
            {
                "id": "open",
                "operation": "navigate",
                "arguments": {"url": {"parameter": "url"}},
                "timeout_ms": 1000,
            }
        ],
    }


@pytest.fixture
def services(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    metadata.create_all(engine)
    browser_metadata.create_all(engine)
    store = LcaStore(engine)
    browser = BrowserService(
        BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")
    )
    return LcaService(store, browser), browser


def count(browser, table):
    with browser.store.engine.connect() as connection:
        return connection.scalar(
            select(func.count()).select_from(
                browser_metadata.tables["tap_browser_" + table]
            )
        )


def create(service):
    return service.create_flow(actor(), "create-key", "Fixture", definition())


def test_save_generate_do_not_execute_and_revisions_conflict(services):
    service, browser = services
    flow = create(service)
    assert create(service)["id"] == flow["id"]
    generated = service.generate_draft(
        actor(), flow["id"], {"url": "https://example.com"}
    )
    assert "page.goto" in generated.source
    saved = service.save_draft(actor(), flow["id"], 1, definition())
    assert saved["version"] == 2
    with pytest.raises(LcaConflict):
        service.save_draft(actor(), flow["id"], 1, definition())
    assert count(browser, "executions") == 0
    assert count(browser, "bundles") == 0


@pytest.mark.parametrize("failure", ["lost_response", "link_crash"])
def test_retry_recovers_original_intent_after_draft_changes(
    services, monkeypatch, failure
):
    service, browser = services
    flow = create(service)
    original_enqueue = browser.create_execution
    original_link = service.store.link_execution

    def lose_response(*args):
        original_enqueue(*args)
        raise TimeoutError("response lost after durable enqueue")

    def fail_link(*args):
        raise RuntimeError("process died before link")

    if failure == "lost_response":
        monkeypatch.setattr(browser, "create_execution", lose_response)
    else:
        monkeypatch.setattr(service.store, "link_execution", fail_link)
    with pytest.raises((TimeoutError, RuntimeError)):
        service.debug_run(
            actor(), flow["id"], 1, {"url": "https://example.com"}, "run-key"
        )
    intent = service.store.get_run_by_key("project-a", "alice", "run-key")
    assert intent and intent["execution_id"] is None
    assert count(browser, "executions") == 1
    changed = definition()
    changed["steps"][0]["timeout_ms"] = 2000
    service.save_draft(actor(), flow["id"], 1, changed)
    frozen = service.store.get_snapshot("project-a", "alice", intent["snapshot_id"])
    assert (
        frozen["canonical_bytes"]
        != service.get_flow(actor(), flow["id"])["canonical_bytes"]
    )
    monkeypatch.setattr(service.store, "link_execution", original_link)

    def forbid_enqueue(*args):
        pytest.fail("must query durable receipt before considering enqueue")

    monkeypatch.setattr(browser, "create_execution", forbid_enqueue)
    recovered = service.debug_run(
        actor(), flow["id"], 1, {"url": "https://example.com"}, "run-key"
    )
    assert recovered["id"] == intent["id"]
    assert recovered["execution_id"]
    browser.store.update(
        "execution", "project-a", recovered["execution_id"], status="unknown"
    )
    assert service.get_run(actor(), recovered["id"])["execution"]["status"] == "unknown"
    assert (
        service.debug_run(
            actor(), flow["id"], 1, {"url": "https://example.com"}, "run-key"
        )["id"]
        == intent["id"]
    )
    assert count(browser, "executions") == 1


def test_changed_replay_key_body_rejected(services):
    service, browser = services
    flow = create(service)
    service.debug_run(actor(), flow["id"], 1, {"url": "https://example.com"}, "run-key")
    for flow_id, version, params in [
        (flow["id"], 2, {"url": "https://example.com"}),
        (flow["id"], 1, {"url": "https://example.com/changed"}),
        ("other-flow", 1, {"url": "https://example.com"}),
    ]:
        with pytest.raises(LcaConflict):
            service.debug_run(actor(), flow_id, version, params, "run-key")
    assert count(browser, "executions") == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"principal_id": "bob"},
        {"project_id": "project-b"},
        {"audience": "tap-browser"},
        {"actions": frozenset()},
        {"enabled": False},
        {"expires_at": datetime(2000, 1, 1, tzinfo=UTC)},
        {"principal_type": "service"},
    ],
)
def test_authorization_covers_reads_writes_generation_and_execution(services, changes):
    service, browser = services
    flow = create(service)
    run = service.debug_run(
        actor(), flow["id"], 1, {"url": "https://example.com"}, "run-key"
    )
    wrong = actor(**changes)
    operations = [
        lambda: service.get_flow(wrong, flow["id"]),
        lambda: service.save_draft(wrong, flow["id"], 1, definition()),
        lambda: service.generate_draft(
            wrong, flow["id"], {"url": "https://example.com"}
        ),
        lambda: service.debug_run(
            wrong, flow["id"], 1, {"url": "https://example.com"}, "other-key"
        ),
        lambda: service.get_run(wrong, run["id"]),
        lambda: service.list_runs(wrong, flow["id"]),
    ]
    for operation in operations:
        with pytest.raises((PermissionError, LcaNotFound)):
            operation()
    assert count(browser, "executions") == 1


def test_retry_after_failure_before_enqueue_uses_persisted_request(
    services, monkeypatch
):
    service, browser = services
    flow = create(service)
    enqueue = browser.create_execution

    def fail_before_enqueue(*args):
        raise TimeoutError("failed before enqueue")

    monkeypatch.setattr(browser, "create_execution", fail_before_enqueue)
    with pytest.raises(TimeoutError):
        service.debug_run(actor(), flow["id"], 1, {"url": "https://example.com"}, "key")
    intent = service.store.get_run_by_key("project-a", "alice", "key")
    assert intent and count(browser, "executions") == 0
    service.save_draft(actor(), flow["id"], 1, definition())
    monkeypatch.setattr(browser, "create_execution", enqueue)
    run = service.debug_run(
        actor(), flow["id"], 1, {"url": "https://example.com"}, "key"
    )
    assert run["id"] == intent["id"]
    assert count(browser, "executions") == 1


def test_invalid_parameters_and_unsupported_origins_do_not_publish_snapshot(services):
    service, browser = services
    flow = create(service)
    with pytest.raises(ValueError):
        service.debug_run(actor(), flow["id"], 1, {}, "missing-input")
    too_many = definition()
    too_many["allowed_origins"] = [f"https://site-{i}.example.com" for i in range(9)]
    with pytest.raises(ValueError):
        service.create_flow(actor(), "many-origins", "Too many", too_many)
    with service.store.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(
                    metadata.tables["tap_lca_debug_snapshots"]
                )
            )
            == 0
        )
    assert count(browser, "bundles") == 0


def test_history_returns_frozen_definition_after_draft_edit(services):
    service, _ = services
    flow = create(service)
    run = service.debug_run(
        actor(), flow["id"], 1, {"url": "https://example.com"}, "history"
    )
    changed = definition()
    changed["steps"][0]["id"] = "changed"
    service.save_draft(actor(), flow["id"], 1, changed)
    historical = service.get_run(actor(), run["id"])["definition_snapshot"]
    assert historical["version"] == 1
    assert historical["definition"]["steps"][0]["id"] == "open"
    assert historical["digest"] == run["definition_digest"]


def test_cancel_and_evidence_require_run_owner_and_match_execution(services):
    from tap_platform.browser_automation.objects import BrowserEvidenceSink

    service, browser = services
    flow = create(service)
    run = service.debug_run(
        actor(), flow["id"], 1, {"url": "https://example.com"}, "evidence"
    )
    allowed = actor(actions=actor().actions | {"lca.runs.cancel", "lca.evidence.read"})
    canceled = service.cancel_run(allowed, run["id"])
    assert canceled["execution_id"] == run["execution_id"]
    assert browser.store.get("execution", "project-a", run["execution_id"])[
        "cancel_requested"
    ]
    sink = BrowserEvidenceSink(browser.store, browser.objects)
    own = sink.persist(
        "project-a", flow["id"], run["execution_id"], "result", b'{"ok":true}', 1000
    )
    foreign = sink.persist(
        "project-a", flow["id"], "other-execution", "result", b'{"private":true}', 1000
    )
    assert (
        service.read_evidence(allowed, run["id"], own.object_ref)[1] == b'{"ok":true}'
    )
    with pytest.raises(PermissionError):
        service.read_evidence(allowed, run["id"], foreign.object_ref)
    with pytest.raises(LcaNotFound):
        service.read_evidence(
            actor(principal_id="bob", actions=allowed.actions),
            run["id"],
            own.object_ref,
        )
    with pytest.raises(PermissionError):
        service.cancel_run(actor(), run["id"])


@pytest.mark.parametrize(
    "corruption", [None, "digest", "id", "status", "status_shape", "duplicate"]
)
def test_canonical_results_are_bound_to_frozen_snapshot(services, corruption):
    service, browser = services
    flow = create(service)
    run = service.debug_run(
        actor(), flow["id"], 1, {"url": "https://example.com"}, "mapped"
    )
    output = {
        "schema_version": "lca-debug-result-v1",
        "definition_digest": run["definition_digest"],
        "steps": [{"step_id": "open", "status": "failed"}],
    }
    if corruption == "digest":
        output["definition_digest"] = "wrong"
    if corruption == "id":
        output["steps"][0]["step_id"] = "not-recorded"
    if corruption == "status":
        output["steps"][0]["status"] = "invented"
    if corruption == "status_shape":
        output["steps"][0]["status"] = {}
    outputs = [output, output] if corruption == "duplicate" else [output]
    browser.store.update(
        "execution",
        "project-a",
        run["execution_id"],
        status="failed",
        result={"outputs": outputs},
    )
    history = service.get_run(actor(), run["id"])
    if corruption is None:
        assert history["canonical_results"] == [{"step_id": "open", "status": "failed"}]
        assert history["canonical_results_error"] is None
    else:
        assert history["canonical_results"] is None
        assert history["canonical_results_error"]


def test_reconcile_by_durable_run_id_survives_reload_without_original_client_key(
    services, monkeypatch
):
    service, browser = services
    flow = create(service)
    original = service.store.link_execution
    monkeypatch.setattr(
        service.store,
        "link_execution",
        lambda *args: (_ for _ in ()).throw(RuntimeError("process died")),
    )
    with pytest.raises(RuntimeError):
        service.debug_run(
            actor(), flow["id"], 1, {"url": "https://example.com"}, "lost-key"
        )
    intent = service.list_runs(actor(), flow["id"])[0]
    assert intent["execution_id"] is None
    monkeypatch.setattr(service.store, "link_execution", original)
    new_service = LcaService(service.store, browser)
    recovered = new_service.reconcile_run(actor(), intent["id"])
    assert recovered["execution_id"]
    assert count(browser, "executions") == 1
    assert new_service.reconcile_run(actor(), intent["id"]) == recovered
    with pytest.raises(LcaNotFound):
        new_service.reconcile_run(actor(principal_id="bob"), intent["id"])
