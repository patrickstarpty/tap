"""Explicitly opt in to the bounded, sanitized embedding research profile."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from tap.modules.knowledge.adapters.litellm import LiteLLMAdapter
from tap.operations.milvus.embeddings import (
    DEFAULT_MAX_CHUNKS,
    DEFAULT_MAX_QUERIES,
    FileEmbeddingCache,
    generate_snapshot,
    load_fixture_inputs,
    research_litellm_config,
    write_research_report,
    write_vector_snapshot,
)

_DOC_FIXTURE = Path("apps/backend/tests/fixtures/milvus/doc-fixture-v1.json")
_QUERY_FIXTURE = Path("apps/backend/tests/fixtures/milvus/query-cases-v1.json")
_CACHE_DIRECTORY = Path(".local/milvus-embedding-cache")
_REPORT_PATH = Path(".local/milvus-research/report.json")
_CANDIDATE_SNAPSHOT_PATH = Path(
    ".local/milvus-research/vectors-research-embedding-v1.json"
)


async def _run(args: argparse.Namespace, settings: Mapping[str, str]) -> None:
    chunks, queries = load_fixture_inputs(args.doc_fixture, args.query_fixture)
    adapter = LiteLLMAdapter(research_litellm_config(settings))
    try:
        snapshot, report = await generate_snapshot(
            adapter,
            chunks,
            queries,
            FileEmbeddingCache(args.cache_directory),
            max_chunks=args.max_chunks,
            max_queries=args.max_queries,
        )
    finally:
        await adapter.close()
    # The report is the completion marker, so the ignored candidate is durable first.
    write_vector_snapshot(args.candidate_snapshot, snapshot)
    write_research_report(args.report, report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded paid embedding research")
    parser.add_argument("--doc-fixture", type=Path, default=_DOC_FIXTURE)
    parser.add_argument("--query-fixture", type=Path, default=_QUERY_FIXTURE)
    parser.add_argument("--cache-directory", type=Path, default=_CACHE_DIRECTORY)
    parser.add_argument("--report", type=Path, default=_REPORT_PATH)
    parser.add_argument(
        "--candidate-snapshot", type=Path, default=_CANDIDATE_SNAPSHOT_PATH
    )
    parser.add_argument("--max-chunks", type=int, default=DEFAULT_MAX_CHUNKS)
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("TAP_RUN_PAID_EMBEDDING_RESEARCH") != "1":
        print("Paid embedding research requires explicit opt-in.", file=sys.stderr)
        return 2
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args, dict(os.environ)))
    except (Exception, asyncio.CancelledError, KeyboardInterrupt):
        print("Embedding research failed.", file=sys.stderr)
        return 1
    print("Embedding research passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
