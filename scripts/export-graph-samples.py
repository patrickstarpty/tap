#!/usr/bin/env python3
"""Export a deterministic random sample of Graph edges or cross-source merge
candidates as CSV for human review (spec 4.3).

Reads the current `READY` `graph_project_*` version (or `--graph-version`,
rejected if it has no `READY` row) off a running Tapper MySQL database. Edge
candidates are every `EXTRACTED` edge; their evidence snippet and source
title are resolved through `MysqlGraphNodeEnrichment` — the same scoped,
`deleted_at`-filtered, 300-char-truncating, `ArtifactError`-only-catching path
`GET /nodes/{node_id}` already uses — rather than ad hoc queries here. Merge
candidates are every node whose sources (`graph_project_node_source`) span
>=2 distinct `source_revision_id`s.

Exits non-zero (after still writing the CSV) when any sampled edge's snippet
or source title could not be resolved, since a human reviewer cannot judge a
blank row; warns (without failing) when fewer rows were available than
`--count` requested.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.entrypoints.tapper_runtime import TapperSettings, _create_blob, _open_database
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql_node_enrichment import MysqlGraphNodeEnrichment
from tap.modules.graph.adapters.mysql_project import (
    graph_merge_log,
    graph_project_edge,
    graph_project_edge_evidence,
    graph_project_node,
    graph_project_node_source,
    graph_project_version,
)
from tap.platform.db.project_scope import scope_predicates
from tap.quality.graph_samples import (
    EDGE_COLUMNS,
    MERGE_COLUMNS,
    edge_csv_row,
    is_cross_source,
    merge_csv_row,
    sample_rows,
)


def _rows(result: Result[Any]) -> list[dict[str, object]]:
    """Materialize a `select(...)` result into plain `dict`s: `RowMapping` is not
    treated as a `Mapping[str, object]` by mypy, and the pure `graph_samples`
    functions take `Mapping[str, object]`."""
    return [dict(row) for row in result.mappings().all()]


async def _resolve_version(session: AsyncSession, graph_version: int | None) -> int:
    """Return the version to sample: `graph_version` if it names a `READY`
    row, the current `READY` version if omitted; raises `ValueError` either
    way nothing qualifies, so an operator sees a clear message instead of an
    empty/wrong-version export."""
    if graph_version is not None:
        exists = (
            await session.execute(
                select(graph_project_version.c.version).where(
                    *scope_predicates(graph_project_version, VALIDATION_SCOPE),
                    graph_project_version.c.version == graph_version,
                    graph_project_version.c.status == "READY",
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise ValueError(f"graph version {graph_version} has no READY row")
        return graph_version
    current = (
        await session.execute(
            select(graph_project_version.c.version)
            .where(
                *scope_predicates(graph_project_version, VALIDATION_SCOPE),
                graph_project_version.c.status == "READY",
            )
            .order_by(graph_project_version.c.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if current is None:
        raise ValueError("project graph has no READY version")
    return int(current)


def _warn_if_fewer_than_requested(kind: str, count: int, sampled: int) -> None:
    if sampled < count:
        print(
            f"warning: requested {count} {kind} samples but only {sampled} are available",
            file=sys.stderr,
        )


async def export_edges(
    sessions: async_sessionmaker[AsyncSession],
    enrichment: MysqlGraphNodeEnrichment,
    *,
    count: int,
    seed: int,
    output: Path,
    graph_version: int | None,
) -> int:
    """Export a deterministic sample of `EXTRACTED` edges as an `EDGE_COLUMNS`
    CSV; `sessions`/`enrichment` are injected so this can run against any
    database/blob backend (including an isolated test one).

    Returns 1 (after still writing the CSV) if any sampled edge ends up with
    a blank snippet or source title; 0 otherwise.
    """
    async with sessions() as session:
        version = await _resolve_version(session, graph_version)
        edge_rows = _rows(
            await session.execute(
                select(graph_project_edge).where(
                    *scope_predicates(graph_project_edge, VALIDATION_SCOPE),
                    graph_project_edge.c.version == version,
                    graph_project_edge.c.origin == "EXTRACTED",
                )
            )
        )
        sampled = sample_rows(edge_rows, count=count, seed=seed)
        _warn_if_fewer_than_requested("edge", count, len(sampled))
        node_rows = _rows(
            await session.execute(
                select(graph_project_node).where(
                    *scope_predicates(graph_project_node, VALIDATION_SCOPE),
                    graph_project_node.c.version == version,
                )
            )
        )
        nodes_by_id = {row["node_id"]: row for row in node_rows}
        csv_rows: list[dict[str, str]] = []
        blank_count = 0
        for edge in sampled:
            evidence_row = (
                (
                    await session.execute(
                        select(graph_project_edge_evidence)
                        .where(
                            *scope_predicates(
                                graph_project_edge_evidence, VALIDATION_SCOPE
                            ),
                            graph_project_edge_evidence.c.version == version,
                            graph_project_edge_evidence.c.edge_id == edge["edge_id"],
                        )
                        .order_by(
                            graph_project_edge_evidence.c.source_revision_id,
                            graph_project_edge_evidence.c.chunk_id,
                        )
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            subject = nodes_by_id[edge["source_node_id"]]
            object_ = nodes_by_id[edge["target_node_id"]]
            if evidence_row is None:
                source_title = ""
                snippet = ""
                chunk_id: object = ""
            else:
                source_revision_id = str(evidence_row["source_revision_id"])
                chunk_id = evidence_row["chunk_id"]
                source_title = (
                    await enrichment.source_name(VALIDATION_SCOPE, source_revision_id)
                    or ""
                )
                snippet = (
                    await enrichment.snippet(
                        VALIDATION_SCOPE, source_revision_id, str(chunk_id)
                    )
                    or ""
                )
            if not source_title or not snippet:
                blank_count += 1
            evidence = {
                "source_title": source_title,
                "chunk_id": chunk_id,
                "snippet": snippet,
            }
            csv_rows.append(edge_csv_row(edge, subject, object_, evidence))
    _write_csv(output, EDGE_COLUMNS, csv_rows)
    print(f"exported {len(csv_rows)} edge samples to {output}")
    if blank_count > 0:
        print(
            f"warning: {blank_count} edge sample(s) have a blank snippet or source title",
            file=sys.stderr,
        )
        return 1
    return 0


async def export_merges(
    sessions: async_sessionmaker[AsyncSession],
    enrichment: MysqlGraphNodeEnrichment,
    *,
    count: int,
    seed: int,
    output: Path,
    graph_version: int | None,
) -> int:
    """Export a deterministic sample of cross-source merge candidates as a
    `MERGE_COLUMNS` CSV; `sessions`/`enrichment` are injected so this can run
    against any database/blob backend (including an isolated test one)."""
    async with sessions() as session:
        version = await _resolve_version(session, graph_version)
        source_rows = _rows(
            await session.execute(
                select(graph_project_node_source).where(
                    *scope_predicates(graph_project_node_source, VALIDATION_SCOPE),
                    graph_project_node_source.c.version == version,
                )
            )
        )
        sources_by_node: dict[str, list[dict[str, object]]] = {}
        for row in source_rows:
            sources_by_node.setdefault(str(row["node_id"]), []).append(row)
        cross_source_node_ids = [
            node_id
            for node_id, sources in sources_by_node.items()
            if is_cross_source(sources)
        ]
        node_rows = _rows(
            await session.execute(
                select(graph_project_node).where(
                    *scope_predicates(graph_project_node, VALIDATION_SCOPE),
                    graph_project_node.c.version == version,
                    graph_project_node.c.node_id.in_(cross_source_node_ids),
                )
            )
        )
        sampled = sample_rows(node_rows, count=count, seed=seed)
        _warn_if_fewer_than_requested("merge", count, len(sampled))
        csv_rows: list[dict[str, str]] = []
        for node in sampled:
            node_id = str(node["node_id"])
            sources = sources_by_node[node_id]
            titles: list[dict[str, object]] = []
            for source in sources:
                title = (
                    await enrichment.source_name(
                        VALIDATION_SCOPE, str(source["source_revision_id"])
                    )
                    or ""
                )
                titles.append(
                    {
                        "source_revision_id": source["source_revision_id"],
                        "source_title": title,
                    }
                )
            merge_log_rows = _rows(
                await session.execute(
                    select(graph_merge_log).where(
                        *scope_predicates(graph_merge_log, VALIDATION_SCOPE),
                        graph_merge_log.c.version == version,
                        graph_merge_log.c.node_id == node_id,
                    )
                )
            )
            raw_aliases = node["aliases"]
            aliases = list(raw_aliases) if isinstance(raw_aliases, list) else []
            csv_rows.append(merge_csv_row(node, aliases, titles, merge_log_rows))
    _write_csv(output, MERGE_COLUMNS, csv_rows)
    print(f"exported {len(csv_rows)} merge samples to {output}")
    return 0


def _write_csv(
    output: Path, columns: tuple[str, ...], rows: list[dict[str, str]]
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)


async def _run(arguments: argparse.Namespace) -> int:
    settings = TapperSettings.from_mapping(dict(os.environ))
    engine, sessions = _open_database(settings)
    try:
        blob_store = _create_blob(settings)
        enrichment = MysqlGraphNodeEnrichment(sessions, blob_store)
        if arguments.kind == "edges":
            return await export_edges(
                sessions,
                enrichment,
                count=arguments.count,
                seed=arguments.seed,
                output=arguments.output,
                graph_version=arguments.graph_version,
            )
        return await export_merges(
            sessions,
            enrichment,
            count=arguments.count,
            seed=arguments.seed,
            output=arguments.output,
            graph_version=arguments.graph_version,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="kind", required=True)
    for kind in ("edges", "merges"):
        sub = subparsers.add_parser(kind)
        sub.add_argument("--count", type=int, required=True)
        sub.add_argument("--seed", type=int, required=True)
        sub.add_argument("--output", type=Path, required=True)
        sub.add_argument("--graph-version", type=int, default=None)
    arguments = parser.parse_args(argv)
    try:
        return asyncio.run(_run(arguments))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
