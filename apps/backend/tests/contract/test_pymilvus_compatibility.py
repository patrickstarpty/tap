import pymilvus
from pymilvus import AnnSearchRequest, Function, FunctionType, MilvusClient, RRFRanker


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
