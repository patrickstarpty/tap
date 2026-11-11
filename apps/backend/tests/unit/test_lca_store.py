"""Persistence invariants, including concurrent independent store clients."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine

from tap_platform.lca.store import LcaConflict, LcaNotFound, LcaStore, metadata


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lca.db'}")
    metadata.create_all(engine)
    yield LcaStore(engine)
    engine.dispose()


def flow(store, **kw):
    return store.create_flow("p", "alice", "create", "Fixture", b'{"steps":[]}', **kw)


def snapshot(store):
    f = flow(store)
    return store.freeze_snapshot(
        "p", "alice", f["id"], 1, "v1", "g1", ["https://fixture.test"]
    )


def run(store, **kw):
    s = snapshot(store)
    return store.create_run(
        "p",
        "alice",
        "replay",
        s["id"],
        "bundle",
        "a" * 64,
        {"bundle_id": "bundle", "timeout": 30},
        **kw,
    )


def test_create_retries_preserve_original_identity_and_reject_changed_body(store):
    a = flow(store)
    assert flow(LcaStore(store.engine)) == a
    with pytest.raises(LcaConflict):
        store.create_flow("p", "alice", "create", "Changed", b'{"steps":[]}')
    with pytest.raises(LcaNotFound):
        store.create_flow("p", "bob", "create", "Fixture", b'{"steps":[]}')
    other = store.create_flow("q", "alice", "create", "Fixture", b'{"steps":[]}')
    assert other["id"] != a["id"]


def test_draft_compare_and_swap_preserves_frozen_snapshot(store):
    s = snapshot(store)
    updated = store.save_draft("p", "alice", s["flow_id"], 1, b'{"steps":[1]}')
    assert updated["version"] == 2
    assert (
        store.get_snapshot("p", "alice", s["id"])["canonical_bytes"] == b'{"steps":[]}'
    )
    with pytest.raises(LcaConflict):
        store.save_draft("p", "alice", s["flow_id"], 1, b"{}")
    with pytest.raises(LcaConflict):
        store.freeze_snapshot("p", "alice", s["flow_id"], 1, "v1", "g1", [])
    assert (
        store.get_flow("p", "alice", s["flow_id"])["canonical_bytes"]
        == b'{"steps":[1]}'
    )


@pytest.mark.parametrize("project,actor", [("q", "alice"), ("p", "bob")])
def test_every_read_write_is_owner_project_scoped(store, project, actor):
    r = run(store)
    s = store.get_snapshot("p", "alice", r["snapshot_id"])
    for operation in (
        lambda: store.get_flow(project, actor, s["flow_id"]),
        lambda: store.save_draft(project, actor, s["flow_id"], 1, b"{}"),
        lambda: store.freeze_snapshot(project, actor, s["flow_id"], 1, "v1", "g1", []),
        lambda: store.get_snapshot(project, actor, s["id"]),
        lambda: store.get_run(project, actor, r["id"]),
        lambda: store.link_execution(project, actor, r["id"], "execution"),
        lambda: store.list_runs(project, actor, s["flow_id"]),
        lambda: store.create_run(project, actor, "new", s["id"], "b", "a" * 64, {}),
    ):
        with pytest.raises(LcaNotFound):
            operation()


def test_intent_survives_restart_and_link_is_immutable(store):
    r = run(store)
    restarted = LcaStore(store.engine)
    assert r["execution_id"] is None
    assert r["execution_key"]
    assert restarted.get_run("p", "alice", r["id"]) == r
    linked = restarted.link_execution("p", "alice", r["id"], "execution")
    assert linked["execution_key"] == r["execution_key"]
    assert restarted.link_execution("p", "alice", r["id"], "execution") == linked
    assert run(restarted) == linked
    with pytest.raises(LcaConflict):
        restarted.link_execution("p", "alice", r["id"], "different")
    assert restarted.list_runs("p", "alice", r["flow_id"]) == [linked]


def test_run_key_rejects_changed_frozen_input(store):
    r = run(store)
    for bundle, digest, request in [
        ("changed", "a" * 64, r["execution_request"]),
        ("bundle", "b" * 64, r["execution_request"]),
        ("bundle", "a" * 64, {"timeout": 31}),
    ]:
        with pytest.raises(LcaConflict):
            store.create_run(
                "p", "alice", "replay", r["snapshot_id"], bundle, digest, request
            )


def test_concurrent_draft_writers_only_one_wins(store):
    f = flow(store)
    barrier = Barrier(2)

    def write(value):
        barrier.wait()
        try:
            return LcaStore(store.engine).save_draft("p", "alice", f["id"], 1, value)[
                "version"
            ]
        except LcaConflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, [b'{"x":1}', b'{"x":2}']), key=str) == [
            2,
            "conflict",
        ]


def test_concurrent_create_and_replay_retries_have_one_identity(store):
    barrier = Barrier(2)

    def create(_):
        barrier.wait()
        return run(LcaStore(store.engine))

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(create, range(2)))
    assert results[0] == results[1]


def test_listing_and_run_key_lookup_do_not_reveal_other_owners(store):
    r = run(store)
    assert len(store.list_flows("p", "alice")) == 1
    assert store.list_flows("p", "bob") == []
    assert store.get_run_by_key("p", "alice", "replay") == r
    assert store.get_run_by_key("p", "alice", "absent") is None
    with pytest.raises(LcaNotFound):
        store.get_run_by_key("p", "bob", "replay")


def test_snapshot_retries_reuse_identity_and_hash_exact_bytes(store):
    import hashlib

    first = snapshot(store)
    second = snapshot(LcaStore(store.engine))
    assert second == first
    assert first["definition_digest"] == hashlib.sha256(b'{"steps":[]}').hexdigest()
    first["allowed_origins"].append("https://changed.test")
    assert store.get_snapshot("p", "alice", first["id"])["allowed_origins"] == [
        "https://fixture.test"
    ]


def test_concurrent_different_execution_links_never_overwrite(store):
    intent = run(store)
    barrier = Barrier(2)

    def link(execution_id):
        barrier.wait()
        try:
            return LcaStore(store.engine).link_execution(
                "p", "alice", intent["id"], execution_id
            )["execution_id"]
        except LcaConflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(link, ["execution-one", "execution-two"]))
    assert results.count("conflict") == 1
    winner = next(result for result in results if result != "conflict")
    assert store.get_run("p", "alice", intent["id"])["execution_id"] == winner


def test_replay_retry_remains_same_after_draft_edit(store):
    intent = run(store)
    store.save_draft("p", "alice", intent["flow_id"], 1, b'{"steps":[1]}')
    assert store.get_run_by_key("p", "alice", "replay") == intent
    assert (
        store.create_run(
            "p",
            "alice",
            "replay",
            intent["snapshot_id"],
            "bundle",
            "a" * 64,
            {"bundle_id": "bundle", "timeout": 30},
        )
        == intent
    )
