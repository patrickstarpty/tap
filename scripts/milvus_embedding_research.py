"""Explicitly opt in to the bounded, sanitized embedding research profile."""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

from tap.modules.knowledge.adapters.litellm import LiteLLMAdapter
from tap.operations.milvus.embeddings import (
    DEFAULT_MAX_CHUNKS,
    DEFAULT_MAX_QUERIES,
    EmbeddingResearchRejected,
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
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


async def _run(args: argparse.Namespace, settings: Mapping[str, str]) -> None:
    cache_directory, report_path, candidate_snapshot = _validated_output_paths(args)
    _remove_completion_marker(report_path)
    try:
        chunks, queries = load_fixture_inputs(args.doc_fixture, args.query_fixture)
        adapter = LiteLLMAdapter(research_litellm_config(settings))
        try:
            snapshot, report = await generate_snapshot(
                adapter,
                chunks,
                queries,
                FileEmbeddingCache(cache_directory),
                max_chunks=args.max_chunks,
                max_queries=args.max_queries,
            )
        finally:
            await adapter.close()
        # The report is the completion marker, so the ignored candidate is durable first.
        write_vector_snapshot(candidate_snapshot, snapshot)
        write_research_report(report_path, report)
    except BaseException:
        _remove_completion_marker(report_path)
        raise


def _validated_output_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    root = Path(os.path.abspath(_REPOSITORY_ROOT))
    expected = (
        (args.cache_directory, root / _CACHE_DIRECTORY, True),
        (args.report, root / _REPORT_PATH, False),
        (args.candidate_snapshot, root / _CANDIDATE_SNAPSHOT_PATH, False),
    )
    validated: list[Path] = []
    for supplied, allowed, expects_directory in expected:
        if not isinstance(supplied, Path):
            raise EmbeddingResearchRejected(
                "research output path is outside the fixed profile"
            )
        candidate = Path(
            os.path.abspath(supplied if supplied.is_absolute() else root / supplied)
        )
        if candidate != allowed:
            raise EmbeddingResearchRejected(
                "research output path is outside the fixed profile"
            )
        _reject_unsafe_output_components(
            root, candidate, expects_directory=expects_directory
        )
        validated.append(candidate)
    return validated[0], validated[1], validated[2]


def _reject_unsafe_output_components(
    root: Path,
    path: Path,
    *,
    expects_directory: bool,
) -> None:
    try:
        root_status = root.lstat()
    except OSError as error:
        raise EmbeddingResearchRejected(
            "research output path is unavailable"
        ) from error
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        raise EmbeddingResearchRejected("research output path is unsafe")

    relative = path.relative_to(root)
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            current_status = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise EmbeddingResearchRejected(
                "research output path is unavailable"
            ) from error
        if stat.S_ISLNK(current_status.st_mode):
            raise EmbeddingResearchRejected("research output path contains a symlink")
        is_leaf = index == len(relative.parts) - 1
        if not is_leaf and not stat.S_ISDIR(current_status.st_mode):
            raise EmbeddingResearchRejected(
                "research output path parent is not a directory"
            )
        if is_leaf and (
            expects_directory != stat.S_ISDIR(current_status.st_mode)
            or not expects_directory
            and not stat.S_ISREG(current_status.st_mode)
        ):
            raise EmbeddingResearchRejected(
                "research output path has the wrong file type"
            )


def _remove_completion_marker(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        if path.parent.is_dir():
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as error:
        raise EmbeddingResearchRejected(
            "research completion marker could not be revoked"
        ) from error


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
