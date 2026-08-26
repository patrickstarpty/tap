"""Explicitly opt in to the bounded, sanitized embedding research profile."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import stat
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from tap.modules.knowledge.adapters.litellm import LiteLLMAdapter
from tap.operations.milvus.embeddings import (
    DEFAULT_MAX_CHUNKS,
    DEFAULT_MAX_QUERIES,
    DirectoryFdEmbeddingCache,
    EmbeddingResearchRejected,
    generate_snapshot,
    load_fixture_inputs,
    research_litellm_config,
    write_research_report_at,
    write_vector_snapshot_at,
)

_DOC_FIXTURE = Path("apps/backend/tests/fixtures/milvus/doc-fixture-v1.json")
_QUERY_FIXTURE = Path("apps/backend/tests/fixtures/milvus/query-cases-v1.json")
_CACHE_DIRECTORY = Path(".local/milvus-embedding-cache")
_REPORT_PATH = Path(".local/milvus-research/report.json")
_CANDIDATE_SNAPSHOT_PATH = Path(
    ".local/milvus-research/vectors-research-embedding-v1.json"
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REPORT_NAME = _REPORT_PATH.name
_CANDIDATE_SNAPSHOT_NAME = _CANDIDATE_SNAPSHOT_PATH.name
_DIRECTORY_FLAGS = (
    getattr(os, "O_RDONLY", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


@dataclass(frozen=True, slots=True)
class _OutputDirectories:
    cache_fd: int = field(repr=False)
    research_fd: int = field(repr=False)


async def _run(args: argparse.Namespace, settings: Mapping[str, str]) -> None:
    with _open_output_directories(args) as outputs:
        _revoke_completion_marker(outputs.research_fd)
        try:
            chunks, queries = load_fixture_inputs(args.doc_fixture, args.query_fixture)
            adapter = LiteLLMAdapter(research_litellm_config(settings))
            try:
                snapshot, report = await generate_snapshot(
                    adapter,
                    chunks,
                    queries,
                    DirectoryFdEmbeddingCache(outputs.cache_fd),
                    max_chunks=args.max_chunks,
                    max_queries=args.max_queries,
                )
            finally:
                await adapter.close()
            # The report is the completion marker, so the candidate is durable first.
            write_vector_snapshot_at(
                outputs.research_fd,
                _CANDIDATE_SNAPSHOT_NAME,
                snapshot,
            )
            write_research_report_at(outputs.research_fd, _REPORT_NAME, report)
        except BaseException as primary:
            _best_effort_remove_completion_marker(outputs.research_fd, primary)
            raise


@contextmanager
def _open_output_directories(args: argparse.Namespace) -> Iterator[_OutputDirectories]:
    _validate_output_arguments(args)
    if not _dirfd_capabilities_available():
        raise EmbeddingResearchRejected("required directory capability is unavailable")
    descriptors: list[int] = []
    try:
        root_fd = _open_trusted_repository()
        descriptors.append(root_fd)
        local_fd = _open_or_create_directory_at(root_fd, ".local")
        descriptors.append(local_fd)
        cache_fd = _open_or_create_directory_at(local_fd, "milvus-embedding-cache")
        descriptors.append(cache_fd)
        research_fd = _open_or_create_directory_at(local_fd, "milvus-research")
        descriptors.append(research_fd)
        _validate_regular_or_absent_at(research_fd, _REPORT_NAME)
        _validate_regular_or_absent_at(research_fd, _CANDIDATE_SNAPSHOT_NAME)
        _probe_directory_capability(research_fd)
        yield _OutputDirectories(cache_fd=cache_fd, research_fd=research_fd)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_output_arguments(args: argparse.Namespace) -> None:
    root = Path(os.path.abspath(_REPOSITORY_ROOT))
    expected = (
        (args.cache_directory, root / _CACHE_DIRECTORY),
        (args.report, root / _REPORT_PATH),
        (args.candidate_snapshot, root / _CANDIDATE_SNAPSHOT_PATH),
    )
    for supplied, allowed in expected:
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


def _dirfd_capabilities_available() -> bool:
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_CLOEXEC")
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.unlink in os.supports_dir_fd
    )


def _open_trusted_repository() -> int:
    try:
        return os.open(Path(os.path.abspath(_REPOSITORY_ROOT)), _DIRECTORY_FLAGS)
    except (OSError, NotImplementedError) as error:
        raise EmbeddingResearchRejected(
            "research output directory capability is unavailable"
        ) from error


def _open_or_create_directory_at(parent_fd: int, name: str) -> int:
    try:
        try:
            return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except (OSError, NotImplementedError) as error:
        raise EmbeddingResearchRejected(
            "research output path is unsafe or unavailable"
        ) from error


def _validate_regular_or_absent_at(directory_fd: int, name: str) -> None:
    try:
        status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except (OSError, NotImplementedError) as error:
        raise EmbeddingResearchRejected(
            "research output path is unavailable"
        ) from error
    if not stat.S_ISREG(status.st_mode):
        raise EmbeddingResearchRejected(
            "research output path contains a symlink or wrong file type"
        )


def _probe_directory_capability(directory_fd: int) -> None:
    source = f".dirfd-probe-{secrets.token_hex(8)}"
    target = f".dirfd-probe-{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            source,
            target,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
        os.unlink(target, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except (OSError, TypeError, NotImplementedError) as error:
        raise EmbeddingResearchRejected(
            "required directory capability is unavailable"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for name in (source, target):
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                pass


def _remove_completion_marker_at(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass
    os.fsync(directory_fd)


def _revoke_completion_marker(directory_fd: int) -> None:
    try:
        _remove_completion_marker_at(directory_fd, _REPORT_NAME)
    except BaseException as error:
        raise EmbeddingResearchRejected(
            "research completion marker could not be revoked"
        ) from error


def _best_effort_remove_completion_marker(
    directory_fd: int,
    primary: BaseException,
) -> None:
    for _attempt in range(2):
        try:
            _remove_completion_marker_at(directory_fd, _REPORT_NAME)
            return
        except BaseException:
            continue
    diagnostic = "embedding research completion marker cleanup was incomplete"
    primary.add_note(diagnostic)
    if primary.__cause__ is None:
        primary.__cause__ = EmbeddingResearchRejected(diagnostic)
        primary.__suppress_context__ = True


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
