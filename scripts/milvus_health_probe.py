"""CLI for local Milvus behavioral health and reader-only canary checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from collections.abc import Mapping

from pymilvus import (  # type: ignore[import-untyped]
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
)
from pymilvus.exceptions import MilvusException  # type: ignore[import-untyped]

from tap.modules.knowledge.adapters.milvus.transport import MilvusQueryRequest
from tap.operations.milvus.client import (
    MilvusSdk,
    build_probe_clients,
    build_reader_client,
    close_probe_clients,
    suppress_pymilvus_rpc_logging,
)
from tap.operations.milvus.health import MilvusHealthCleanupFailed, run_health_probe

_HEALTH_MAX_ATTEMPTS = 4
_HEALTH_RETRY_DELAY_SECONDS = 3.0


async def _run_health(settings: Mapping[str, str]) -> None:
    clients = build_probe_clients(settings, sdk=_sdk())
    failure: BaseException | None = None
    try:
        probe_id = f"probe_{secrets.token_hex(8)}"
        report = await run_health_probe(clients, probe_id)
        if (
            report.allowed_hits != 1
            or report.denied_hits != 0
            or not report.cleanup_complete
        ):
            raise RuntimeError("Milvus behavioral health assertions failed")
    except BaseException as error:
        failure = error
    try:
        await close_probe_clients(clients)
    except Exception:
        if failure is None or (
            isinstance(failure, Exception)
            and not isinstance(failure, MilvusHealthCleanupFailed)
        ):
            failure = MilvusHealthCleanupFailed("Milvus health client cleanup failed")
        else:
            failure.add_note("Milvus health client cleanup also failed")
    if failure is not None:
        raise failure


async def _run_health_until_ready(settings: Mapping[str, str]) -> None:
    """Retry the full behavioral probe while Milvus finishes functional startup."""
    for attempt in range(1, _HEALTH_MAX_ATTEMPTS + 1):
        try:
            await _run_health(settings)
        except MilvusHealthCleanupFailed:
            raise
        except Exception:
            if attempt == _HEALTH_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(_HEALTH_RETRY_DELAY_SECONDS)
        else:
            return


async def _run_reader_canary(settings: Mapping[str, str]) -> None:
    alias = _required(settings, "TAP_MILVUS_DOC_ALIAS")
    chunk_id = _required(settings, "TAP_MILVUS_READINESS_CHUNK_ID")
    reader = build_reader_client(settings, sdk=_sdk())
    try:
        collection_name = await reader.describe_alias(alias)
        rows = await reader.query(
            MilvusQueryRequest(
                collection_name=collection_name,
                filter_expression=f"chunk_id == {json.dumps(chunk_id)}",
                output_fields=("chunk_id",),
                limit=2,
            )
        )
        if rows != ({"chunk_id": chunk_id},):
            raise RuntimeError("Milvus reader canary did not return exactly one row")
    finally:
        await reader.close()


def _required(settings: Mapping[str, str], name: str) -> str:
    value = settings.get(name)
    if not isinstance(value, str) or not value or len(value) > 1_024:
        raise ValueError(f"{name} is required")
    return value


def _sdk() -> MilvusSdk:
    return MilvusSdk(
        client_factory=MilvusClient,
        create_schema=MilvusClient.create_schema,
        function_factory=Function,
        ann_search_request_factory=AnnSearchRequest,
        ranker_factory=RRFRanker,
        varchar_type=DataType.VARCHAR,
        sparse_vector_type=DataType.SPARSE_FLOAT_VECTOR,
        float_vector_type=DataType.FLOAT_VECTOR,
        array_type=DataType.ARRAY,
        int64_type=DataType.INT64,
        bool_type=DataType.BOOL,
        bm25_function_type=FunctionType.BM25,
        permission_error=MilvusException,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local Milvus behavior")
    parser.add_argument("--reader-canary", action="store_true")
    args = parser.parse_args()
    try:
        with suppress_pymilvus_rpc_logging():
            settings = dict(os.environ)
            if args.reader_canary:
                asyncio.run(_run_reader_canary(settings))
            else:
                asyncio.run(_run_health_until_ready(settings))
    except Exception:
        print("Milvus health probe failed.", file=sys.stderr)
        return 1
    print("Milvus health probe passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
