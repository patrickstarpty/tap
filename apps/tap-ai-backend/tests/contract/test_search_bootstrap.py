from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from test_milvus_search_strict import RecordingAuditSink, RecordingReader

from tap.entrypoints.knowledge_bootstrap import build_search_port
from tap.modules.knowledge.adapters.milvus.audit import SearchAuditSink
from tap.modules.knowledge.adapters.milvus.config import MilvusSearchConfig
from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.adapters.milvus.transport import MilvusReader
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.search import SearchPort


def milvus_settings() -> dict[str, str]:
    return {
        "TAP_SEARCH_BACKEND": "milvus",
        "MILVUS_URI": "http://127.0.0.1:19530",
        "MILVUS_DATABASE": "tap_local",
        "MILVUS_READER_USERNAME": "tap_reader",
        "MILVUS_READER_PASSWORD": "tap-local-reader",
        "TAP_MILVUS_DOC_ALIAS": "kb_doc_active",
        "TAP_MILVUS_DOC_PHYSICAL_PREFIX": "kb_doc_v1_",
        "TAP_MILVUS_DOC_SCHEMA_VERSION": "doc-schema-v1",
        "TAP_MILVUS_DOC_SCHEMA_SHA256": "sha256:" + "c" * 64,
        "TAP_MILVUS_DOC_CORPUS_VERSION": "corpus-fixture-v1",
        "TAP_MILVUS_DOC_EMBEDDING_MODEL": "research-embedding-v1",
        "TAP_MILVUS_DOC_VECTOR_DIMENSION": "1536",
    }


def factories(
    *,
    reader: MilvusReader | None = None,
) -> tuple[list[MilvusSearchConfig], Callable[[MilvusSearchConfig], MilvusReader]]:
    milvus_calls: list[MilvusSearchConfig] = []

    def milvus_factory(config: MilvusSearchConfig) -> MilvusReader:
        milvus_calls.append(config)
        return reader or RecordingReader()

    return milvus_calls, milvus_factory


def build(
    settings: Mapping[str, str],
    *,
    audit: SearchAuditSink | None = None,
) -> tuple[SearchPort, list[MilvusSearchConfig]]:
    milvus_calls, milvus_factory = factories()
    port = build_search_port(
        settings,
        milvus_reader_factory=milvus_factory,
        audit_sink=audit or RecordingAuditSink(),
    )
    return port, milvus_calls


def test_milvus_selection_validates_config_and_calls_only_reader_factory_once() -> None:
    """Repeated reader creation would make selection implicit."""
    port, milvus_calls = build(milvus_settings())

    assert isinstance(port, MilvusSearchAdapter)
    assert len(milvus_calls) == 1
    configured = milvus_calls[0]
    assert configured.username == "tap_reader"
    assert configured.password.get_secret_value() == "tap-local-reader"
    assert tuple(configured.targets) == (SourceFamily.DOC,)
    assert configured.targets[SourceFamily.DOC].vector_dimension == 1536


@pytest.mark.parametrize("backend", (None, "", "Milvus", "azure", "unknown"))
def test_backend_selection_is_required_and_closed(backend: str | None) -> None:
    """Defaulting or normalizing provider names would make rollout selection implicit."""
    settings = {} if backend is None else {"TAP_SEARCH_BACKEND": backend}
    _, milvus_factory = factories()

    with pytest.raises(ValueError, match="TAP_SEARCH_BACKEND"):
        build_search_port(
            settings,
            milvus_reader_factory=milvus_factory,
            audit_sink=RecordingAuditSink(),
        )


@pytest.mark.parametrize(
    "missing",
    tuple(key for key in milvus_settings() if key != "TAP_SEARCH_BACKEND"),
)
def test_milvus_selection_requires_each_fixed_setting(missing: str) -> None:
    """Provider config defaults would silently select an unreviewed target or identity."""
    settings = milvus_settings()
    del settings[missing]
    _, milvus_factory = factories()

    with pytest.raises(ValueError, match=missing):
        build_search_port(
            settings,
            milvus_reader_factory=milvus_factory,
            audit_sink=RecordingAuditSink(),
        )


@pytest.mark.parametrize("dimension", ("", "0", "1536.0", "not-an-int"))
def test_milvus_vector_dimension_setting_is_a_strict_positive_integer(dimension: str) -> None:
    """Coercing a malformed dimension would weaken vector-space binding."""
    settings = {**milvus_settings(), "TAP_MILVUS_DOC_VECTOR_DIMENSION": dimension}
    _, milvus_factory = factories()

    with pytest.raises(ValueError, match="TAP_MILVUS_DOC_VECTOR_DIMENSION"):
        build_search_port(
            settings,
            milvus_reader_factory=milvus_factory,
            audit_sink=RecordingAuditSink(),
        )
