import inspect
from dataclasses import fields

import pymilvus
from pymilvus import (
    AnnSearchRequest,
    AsyncMilvusClient,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
)
from pymilvus.client.async_grpc_handler import AsyncGrpcHandler  # type: ignore[import-untyped]
from pymilvus.client.connection_manager import (  # type: ignore[import-untyped]
    AsyncConnectionManager,
    AsyncRegularStrategy,
    ManagedConnection,
)


def test_pymilvus_surface_is_pinned_for_python_313() -> None:
    assert pymilvus.__version__ == "2.6.17"
    for name in (
        "describe_alias",
        "describe_collection",
        "describe_index",
        "hybrid_search",
        "create_schema",
        "alter_alias",
        "grant_privilege_v2",
        "run_analyzer",
    ):
        assert callable(getattr(MilvusClient, name))
    assert AnnSearchRequest is not None
    assert RRFRanker is not None
    assert Function is not None
    assert FunctionType.BM25 is not None


def test_pymilvus_native_async_reader_surface_is_pinned_and_cancellable() -> None:
    for name in (
        "describe_alias",
        "describe_collection",
        "describe_index",
        "hybrid_search",
        "query",
        "close",
    ):
        assert inspect.iscoroutinefunction(getattr(AsyncMilvusClient, name))


def test_pymilvus_private_dedicated_acquisition_shape_is_explicitly_pinned() -> None:
    manager = AsyncConnectionManager()

    assert type(manager._dedicated) is dict
    assert callable(manager._get_lock)
    assert callable(manager._get_strategy)
    assert callable(manager._register_error_callback)
    assert inspect.iscoroutinefunction(AsyncGrpcHandler.ensure_channel_ready)
    assert inspect.iscoroutinefunction(AsyncGrpcHandler.close)
    assert tuple(field.name for field in fields(ManagedConnection)) == (
        "handler",
        "config",
        "strategy",
        "created_at",
        "last_used_at",
        "clients",
        "recovery_gen",
        "connect_timeout",
    )
    strategy = AsyncRegularStrategy()
    assert callable(strategy.create_handler)
    assert inspect.iscoroutinefunction(strategy.close_async)
