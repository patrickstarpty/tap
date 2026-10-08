#!/usr/bin/env python3
"""Synthetic project-graph performance gate (spec 4.4): `generate` writes a
deterministic `tap.quality.graph_bench.synthesize_graph` benchmark graph as
one `graph_project_*` version; `run` loads that version once (recording load
time) and times `neighbors`/`path`/`overview` queries (20 warmup, N=200 by
default, `time.perf_counter()`), gating each kind's p95 against
`P95_LIMIT_MS`; `cleanup` deletes every `graph_project_*` row this tool wrote.
Workflow: `generate` -> `run` -> `cleanup`.

Not part of the default CI/`make test` run (`make graph-bench`, opt-in): all
three subcommands require `TAP_DATABASE_URL` and talk to a real MySQL
database. They always target a dedicated `BENCH_SCOPE` (enterprise `local`,
project `graph-bench`) — never the Tapper Demo validation scope
(`tapper-demo`) — so a benchmark run can never become the demo's current
graph version, get picked up by a real merge job, or get pruned/left behind
in a shared database; `generate` seeds `BENCH_SCOPE`'s `project` and
`actor_principal` rows itself (idempotently) since nothing else does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.adapters.mysql import actor_principal, project
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.graph.adapters.mysql_project import (
    graph_merge_log,
    graph_project_alias,
    graph_project_community,
    graph_project_edge,
    graph_project_edge_evidence,
    graph_project_merge_job,
    graph_project_node,
    graph_project_node_source,
    graph_project_version,
    publish_project_version,
)
from tap.modules.graph.adapters.mysql_project_store import MysqlProjectGraphStore
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    Alias,
    Community,
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
)
from tap.platform.db.project_scope import scope_predicates
from tap.platform.db.session import create_engine_and_session_factory
from tap.quality.graph_bench import (
    BENCH_KINDS,
    LOAD_MS_LIMIT_MS,
    P95_LIMIT_MS,
    BenchGraph,
    digest,
    p95_ms,
    synthesize_graph,
)

# Every table `publish_project_version`/`cleanup` touches, in child-to-parent
# delete order (mirrors `mysql_project.prune_project_versions`, plus the
# project-scoped merge-job row, which has no FK dependency on `version`).
_CLEANUP_TABLES = (
    graph_merge_log,
    graph_project_community,
    graph_project_alias,
    graph_project_edge_evidence,
    graph_project_node_source,
    graph_project_edge,
    graph_project_node,
    graph_project_version,
    graph_project_merge_job,
)


def _require_non_demo_scope(scope: ProjectScopeContext) -> ProjectScopeContext:
    """Refuse to ever operate on the Tapper Demo validation scope: a bench
    graph published there would become the demo's current graph version, be
    picked up by a real project-merge job, and be pruned/left behind with
    nothing to clean it up."""
    if (
        scope.enterprise_id == VALIDATION_SCOPE.enterprise_id
        and scope.project_id == VALIDATION_SCOPE.project_id
    ):
        raise ValueError(
            "graph-bench must not target the Tapper Demo validation scope "
            f"({VALIDATION_SCOPE.enterprise_id}/{VALIDATION_SCOPE.project_id})"
        )
    return scope


BENCH_SCOPE = _require_non_demo_scope(
    ProjectScopeContext(
        enterprise_id="local",
        project_id="graph-bench",
        actor_id="graph-bench-bot",
        identity_mode=IdentityMode.VALIDATION,
    )
)


async def _ensure_bench_scope_seeded(session: AsyncSession) -> None:
    """Idempotently seed `BENCH_SCOPE`'s `project` and `actor_principal` rows:
    `graph_project_*` FKs (migration 0029) require both to exist under
    `BENCH_SCOPE.enterprise_id`, and unlike the Tapper Demo scope — seeded by
    migration 0006 — nothing seeds the dedicated bench scope. `enterprise_id
    = "local"` itself is assumed already seeded by migration 0006, which
    every `graph_project_*` FK chain (including the demo scope) relies on."""
    await session.execute(
        mysql_insert(project)
        .values(
            project_id=BENCH_SCOPE.project_id,
            enterprise_id=BENCH_SCOPE.enterprise_id,
            enabled=True,
        )
        .on_duplicate_key_update(enabled=True)
    )
    await session.execute(
        mysql_insert(actor_principal)
        .values(
            actor_id=BENCH_SCOPE.actor_id,
            enterprise_id=BENCH_SCOPE.enterprise_id,
            principal_type="VALIDATION",
            enabled=True,
        )
        .on_duplicate_key_update(enabled=True)
    )


def _draft_from_bench_graph(graph: BenchGraph) -> ProjectGraphDraft:
    """Convert `graph`'s plain-dict rows into the domain objects
    `publish_project_version` writes; every bench edge/alias is generated
    with a fixed `origin` ("EXTRACTED"/"MODEL"), so those are hardcoded here
    rather than round-tripped through an untyped dict value."""
    nodes = tuple(
        ProjectNode(
            node_id=str(row["node_id"]),
            label=str(row["label"]),
            node_type=str(row["node_type"]),
            canonical_key=str(row["canonical_key"]),
            degree=int(row["degree"]),  # type: ignore[call-overload]
            community_id=str(row["community_id"]),
            aliases=(),
        )
        for row in graph.nodes
    )
    edges = tuple(
        ProjectEdge(
            edge_id=str(row["edge_id"]),
            source_node_id=str(row["source_node_id"]),
            target_node_id=str(row["target_node_id"]),
            relation_type=str(row["relation_type"]),
            relation_label=str(row["relation_label"]),
            origin=RelationOrigin.EXTRACTED,
            confidence=float(row["confidence"]),  # type: ignore[arg-type]
        )
        for row in graph.edges
    )
    node_sources = tuple(
        NodeSource(
            node_id=str(row["node_id"]),
            source_revision_id=str(row["source_revision_id"]),
            document_revision_id=str(row["document_revision_id"]),
            chunk_id=str(row["chunk_id"]),
            anchor=row["anchor_json"],  # type: ignore[arg-type]
            fragment_snapshot_id=str(row["fragment_snapshot_id"]),
            fragment_node_id=str(row["fragment_node_id"]),
        )
        for row in graph.node_sources
    )
    edge_evidence = tuple(
        EdgeEvidence(
            edge_id=str(row["edge_id"]),
            source_revision_id=str(row["source_revision_id"]),
            document_revision_id=str(row["document_revision_id"]),
            chunk_id=str(row["chunk_id"]),
            anchor=row["anchor_json"],  # type: ignore[arg-type]
            content_digest=str(row["content_digest"]),
            fragment_snapshot_id=str(row["fragment_snapshot_id"]),
            fragment_edge_id=str(row["fragment_edge_id"]),
        )
        for row in graph.edge_evidence
    )
    aliases = tuple(
        Alias(
            alias_norm=str(row["alias_norm"]),
            node_id=str(row["node_id"]),
            origin="MODEL",
        )
        for row in graph.aliases
    )
    communities = tuple(
        Community(
            community_id=str(row["community_id"]),
            label=str(row["label"]),
            size=int(row["size"]),  # type: ignore[call-overload]
        )
        for row in graph.communities
    )
    return ProjectGraphDraft(
        fragment_digest=digest(graph),
        nodes=nodes,
        edges=edges,
        node_sources=node_sources,
        edge_evidence=edge_evidence,
        aliases=aliases,
        communities=communities,
        merge_log=(),
    )


async def _generate(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: int,
    node_count: int,
    edge_count: int,
    community_count: int,
    source_count: int,
) -> int:
    """Write one bench graph as `graph_project_version=seed` under
    `BENCH_SCOPE`; skips (exit 0) if that version already exists, so
    re-running `generate` with the same `--seed` is a no-op rather than a
    failure. The existence check, the `BENCH_SCOPE` seed, and the publish
    all run in one transaction: `session.execute` autobegins a transaction
    on first use, so starting a *second*, nested `session.begin()` afterward
    (as a separate `async with`) raises `InvalidRequestError` — the fix is
    one `async with sessions() as session, session.begin():` for the whole
    unit of work, the same pattern `mysql_conversations.py` uses."""
    version = seed
    async with sessions() as session, session.begin():
        await _ensure_bench_scope_seeded(session)
        existing = (
            await session.execute(
                select(graph_project_version.c.version).where(
                    *scope_predicates(graph_project_version, BENCH_SCOPE),
                    graph_project_version.c.version == version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(f"graph_project_version {version} already exists; skipping generate")
            return 0
        graph = synthesize_graph(
            seed=seed,
            node_count=node_count,
            edge_count=edge_count,
            community_count=community_count,
            source_count=source_count,
        )
        draft = _draft_from_bench_graph(graph)
        await publish_project_version(
            session, BENCH_SCOPE, draft, version=version, now=datetime.now(UTC)
        )
    print(
        f"generated bench graph seed={seed} nodes={node_count} edges={edge_count} "
        f"-> graph_project_version={version}"
    )
    return 0


async def _measure(
    fn: Callable[[], Awaitable[None]], *, warmup: int, samples: int
) -> list[float]:
    for _ in range(warmup):
        await fn()
    timings: list[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        await fn()
        timings.append((time.perf_counter() - start) * 1000.0)
    return timings


async def _run(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: int,
    samples: int,
    warmup: int,
    report: Path,
) -> int:
    store = MysqlProjectGraphStore(sessions)
    load_start = time.perf_counter()
    # `_loaded` is the store's private per-(project, version) cache-filling
    # load; calling it directly (rather than any query method) isolates pure
    # load time from query time, as the report's separate `loadMs` requires.
    loaded = await store._loaded(BENCH_SCOPE, seed)  # noqa: SLF001
    load_ms = (time.perf_counter() - load_start) * 1000.0
    load_passed = load_ms <= LOAD_MS_LIMIT_MS

    node_ids = sorted(loaded.nodes.keys())
    community_ids = sorted(community.community_id for community in loaded.communities)
    if len(node_ids) < 2:
        raise ValueError("bench graph needs at least 2 nodes to run path queries")
    rng = random.Random(seed)
    path_found: list[bool] = []

    async def neighbors_once() -> None:
        node_id = node_ids[rng.randrange(len(node_ids))]
        await store.neighbors(BENCH_SCOPE, node_id, depth=1, version=seed)

    async def path_once() -> None:
        source, target = rng.sample(node_ids, 2)
        subgraph = await store.path(
            BENCH_SCOPE, source, target, max_hops=3, version=seed
        )
        path_found.append(len(subgraph.nodes) > 0)

    async def overview_once() -> None:
        count = min(rng.randint(1, 3), len(community_ids)) if community_ids else 0
        picked = tuple(rng.sample(community_ids, count)) if count else ()
        await store.overview(
            BENCH_SCOPE, community_ids=picked, node_limit=150, version=seed
        )

    fns = {"neighbors": neighbors_once, "path": path_once, "overview": overview_once}
    queries: dict[str, dict[str, object]] = {}
    overall_passed = True
    for kind in BENCH_KINDS:
        timings = await _measure(fns[kind], warmup=warmup, samples=samples)
        p95 = p95_ms(timings)
        passed = p95 < P95_LIMIT_MS
        overall_passed = overall_passed and passed
        queries[kind] = {
            "samples": samples,
            "p95Ms": p95,
            "meanMs": statistics.fmean(timings),
            "passed": passed,
        }
    # `path_found` collects one bool per `path_once()` call, warmup calls
    # included; only the last `samples` (the timed run) belong in the report.
    timed_path_found = path_found[-samples:] if samples else []
    path_found_rate = (
        sum(timed_path_found) / len(timed_path_found) if timed_path_found else 0.0
    )

    result = {
        "seed": seed,
        "nodeCount": len(loaded.nodes),
        "edgeCount": len(loaded.edges),
        "loadMs": load_ms,
        "loadMsLimitMs": LOAD_MS_LIMIT_MS,
        "loadPassed": load_passed,
        "pathFoundRate": path_found_rate,
        "queries": queries,
        # `loadPassed` is reported only; the brief gates the exit code on the
        # p95 query thresholds alone, not on load time.
        "passed": overall_passed,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if overall_passed else 1


async def _cleanup(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Delete every `graph_project_*` row under `BENCH_SCOPE` (all versions,
    nodes, edges, sources, evidence, aliases, communities, merge log, and the
    merge-job row), scoped by `(enterprise_id, project_id)` so this can never
    touch any other project's rows, including the Tapper Demo scope."""
    async with sessions() as session, session.begin():
        for table in _CLEANUP_TABLES:
            await session.execute(
                delete(table).where(*scope_predicates(table, BENCH_SCOPE))
            )
    print(f"cleaned up graph-bench project-graph rows for {BENCH_SCOPE.project_id}")
    return 0


def _require_database_url() -> str:
    database_url = os.environ.get("TAP_DATABASE_URL")
    if not database_url:
        raise ValueError("TAP_DATABASE_URL is required")
    return database_url


async def _main(arguments: argparse.Namespace) -> int:
    engine, sessions = create_engine_and_session_factory(_require_database_url())
    try:
        if arguments.command == "generate":
            return await _generate(
                sessions,
                seed=arguments.seed,
                node_count=arguments.nodes,
                edge_count=arguments.edges,
                community_count=arguments.communities,
                source_count=arguments.sources,
            )
        if arguments.command == "cleanup":
            return await _cleanup(sessions)
        return await _run(
            sessions,
            seed=arguments.seed,
            samples=arguments.samples,
            warmup=arguments.warmup,
            report=arguments.report,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate",
        help="write a bench graph version (step 1 of generate -> run -> cleanup)",
    )
    generate.add_argument("--seed", type=int, required=True)
    generate.add_argument("--nodes", type=int, default=10_000)
    generate.add_argument("--edges", type=int, default=50_000)
    generate.add_argument("--communities", type=int, default=50)
    generate.add_argument("--sources", type=int, default=40)

    run = subparsers.add_parser(
        "run",
        help="time neighbors/path/overview queries (step 2 of generate -> run -> cleanup)",
    )
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--samples", type=int, default=200)
    run.add_argument("--warmup", type=int, default=20)
    run.add_argument("--report", type=Path, required=True)

    subparsers.add_parser(
        "cleanup",
        help="delete every graph-bench project-graph row (step 3 of generate -> run -> cleanup)",
    )

    arguments = parser.parse_args(argv)
    try:
        return asyncio.run(_main(arguments))
    except (ValueError, LookupError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
