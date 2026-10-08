"""Integration test for `scripts/graph-bench.py` against a real, isolated
MySQL: `generate` a small bench graph, `run` neighbors/path/overview timings
against it, and `cleanup` every row it wrote — all under the dedicated
`BENCH_SCOPE` (never the Tapper Demo validation scope). Requires
`TAP_RUN_MYSQL_INTEGRATION=1` (same convention as `owned_project_mysql`);
skips otherwise."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _load_graph_bench_cli_module() -> ModuleType:
    path = _REPOSITORY_ROOT / "scripts" / "graph-bench.py"
    spec = importlib.util.spec_from_file_location("graph_bench_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(_REPOSITORY_ROOT))
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_generate_run_cleanup_round_trip_on_a_small_bench_graph(
    owned_project_mysql, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_graph_bench_cli_module()
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        seed = 20261006

        generate_result = await module._generate(
            sessions,
            seed=seed,
            node_count=200,
            edge_count=800,
            community_count=10,
            source_count=5,
        )
        assert generate_result == 0
        assert "generated bench graph" in capsys.readouterr().out

        # Re-running `generate` with the same seed must skip, not crash with
        # InvalidRequestError (I1) or re-publish the version.
        repeat_result = await module._generate(
            sessions,
            seed=seed,
            node_count=200,
            edge_count=800,
            community_count=10,
            source_count=5,
        )
        assert repeat_result == 0
        assert "already exists; skipping generate" in capsys.readouterr().out

        async with sessions() as session:
            version_count = await session.scalar(
                select(func.count())
                .select_from(module.graph_project_version)
                .where(
                    module.graph_project_version.c.project_id == module.BENCH_SCOPE.project_id,
                    module.graph_project_version.c.version == seed,
                )
            )
        assert version_count == 1

        report_path = tmp_path / "report.json"
        run_result = await module._run(
            sessions, seed=seed, samples=200, warmup=20, report=report_path
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["seed"] == seed
        assert report["nodeCount"] == 200
        assert report["edgeCount"] == 800
        assert isinstance(report["loadMs"], float)
        assert report["loadMsLimitMs"] == 1000.0
        assert isinstance(report["loadPassed"], bool)
        assert 0.0 <= report["pathFoundRate"] <= 1.0
        assert set(report["queries"]) == {"neighbors", "path", "overview"}
        for kind in ("neighbors", "path", "overview"):
            assert report["queries"][kind]["samples"] == 200
            assert isinstance(report["queries"][kind]["p95Ms"], float)
            assert isinstance(report["queries"][kind]["meanMs"], float)
            assert isinstance(report["queries"][kind]["passed"], bool)
        # A 200-node/800-edge in-memory graph is comfortably inside the
        # p95 < 300ms gate and the <=1s load budget.
        assert report["loadPassed"] is True
        assert report["passed"] is True
        assert run_result == 0

        cleanup_result = await module._cleanup(sessions)
        assert cleanup_result == 0
        assert "cleaned up graph-bench project-graph rows" in capsys.readouterr().out

        async with sessions() as session:
            for table in module._CLEANUP_TABLES:
                remaining = await session.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.project_id == module.BENCH_SCOPE.project_id)
                )
                assert remaining == 0, f"{table.name} still has rows after cleanup"
    finally:
        await engine.dispose()
