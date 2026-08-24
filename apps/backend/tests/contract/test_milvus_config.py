from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import SecretStr

from tap.modules.knowledge.adapters.milvus import MilvusIndexTarget, MilvusSearchConfig
from tap.modules.knowledge.domain.models import SourceFamily


def doc_target() -> MilvusIndexTarget:
    return MilvusIndexTarget(
        family=SourceFamily.DOC,
        alias="kb_doc_active",
        physical_name_prefix="kb_doc_v1_",
        schema_version="doc-schema-v1",
        schema_sha256="sha256:" + "c" * 64,
        corpus_version="corpus-fixture-v1",
        embedding_model_version="research-embedding-v1",
        vector_dimension=1536,
    )


def config_for(**overrides: object) -> MilvusSearchConfig:
    values: dict[str, object] = {
        "uri": "http://127.0.0.1:19530",
        "database": "tap_local",
        "username": "tap_reader",
        "password": SecretStr("reader-secret"),
        "targets": {SourceFamily.DOC: doc_target()},
    }
    values.update(overrides)
    return MilvusSearchConfig(**values)  # type: ignore[arg-type]


def test_milvus_config_hides_password_and_accepts_only_doc_target() -> None:
    target = doc_target()
    config = MilvusSearchConfig(
        uri="http://127.0.0.1:19530",
        database="tap_local",
        username="tap_reader",
        password=SecretStr("reader-secret"),
        targets={SourceFamily.DOC: target},
    )

    assert "reader-secret" not in repr(config)
    assert tuple(config.targets) == (SourceFamily.DOC,)


def test_milvus_config_copies_targets_into_an_immutable_mapping() -> None:
    mutable_targets = {SourceFamily.DOC: doc_target()}
    config = config_for(targets=mutable_targets)

    mutable_targets.clear()

    assert tuple(config.targets) == (SourceFamily.DOC,)
    with pytest.raises(TypeError):
        config.targets[SourceFamily.DOC] = doc_target()  # type: ignore[index]


@pytest.mark.parametrize(
    "uri",
    (
        "http://127.0.0.1:19530",
        "http://localhost:19530",
        "https://milvus.example:19530",
    ),
)
def test_milvus_config_accepts_exact_loopback_http_or_tls(uri: str) -> None:
    assert config_for(uri=uri).uri == uri


@pytest.mark.parametrize(
    "uri",
    (
        "http://milvus.example:19530",
        "http://127.0.0.2:19530",
        "http://[::1]:19530",
        "grpc://127.0.0.1:19530",
        "https://reader:secret@milvus.example:19530",
        "https://milvus.example:19530/path",
        "https://milvus.example:19530?database=tap",
    ),
)
def test_milvus_config_rejects_non_tls_or_credential_bearing_uris(uri: str) -> None:
    with pytest.raises(ValueError, match="URI"):
        config_for(uri=uri)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("alias", "kb-doc-active"),
        ("alias", "9kb_doc_active"),
        ("alias", "kb_doc_active;drop"),
        ("physical_name_prefix", "kb/doc/v1"),
        ("physical_name_prefix", ""),
    ),
)
def test_milvus_target_rejects_unsafe_aliases_and_prefixes(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field.replace("_", " ")):
        replace(doc_target(), **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_sha256", "c" * 64),
        ("schema_sha256", "sha256:" + "C" * 64),
        ("schema_sha256", "sha256:" + "c" * 63),
        ("vector_dimension", 0),
        ("vector_dimension", -1),
        ("vector_dimension", True),
    ),
)
def test_milvus_target_requires_canonical_schema_hash_and_positive_dimension(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(doc_target(), **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("candidate_limit", 0),
        ("candidate_limit", 51),
        ("candidate_limit", True),
        ("timeout_seconds", 0.0),
        ("timeout_seconds", 30.01),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", True),
        ("max_connections", 0),
        ("max_connections", 17),
        ("max_connections", True),
        ("max_filter_bytes", 0),
        ("max_filter_bytes", 32_769),
        ("max_filter_bytes", True),
    ),
)
def test_milvus_config_rejects_values_outside_exact_numeric_bounds(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        config_for(**{field: value})


def test_milvus_config_requires_one_matching_doc_target() -> None:
    code_target = replace(doc_target(), family=SourceFamily.CODE)

    for targets in (
        {},
        {SourceFamily.CODE: code_target},
        {SourceFamily.DOC: code_target},
        {SourceFamily.DOC: doc_target(), SourceFamily.CODE: code_target},
    ):
        with pytest.raises(ValueError, match="doc target"):
            config_for(targets=targets)


def test_milvus_config_rejects_a_raw_string_target_key() -> None:
    with pytest.raises(ValueError, match="doc target"):
        config_for(targets={"doc": doc_target()})
