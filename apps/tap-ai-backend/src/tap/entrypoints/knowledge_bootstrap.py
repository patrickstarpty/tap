"""Explicit composition root for selecting the Knowledge search provider."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from pydantic import SecretStr

from tap.modules.knowledge.adapters.milvus.audit import SearchAuditSink
from tap.modules.knowledge.adapters.milvus.config import (
    MilvusIndexTarget,
    MilvusSearchConfig,
)
from tap.modules.knowledge.adapters.milvus.search import MilvusSearchAdapter
from tap.modules.knowledge.adapters.milvus.transport import MilvusReader
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.search import SearchPort

_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")


def build_search_port(
    settings: Mapping[str, str],
    *,
    milvus_reader_factory: Callable[[MilvusSearchConfig], MilvusReader],
    azure_factory: Callable[[Mapping[str, str]], SearchPort],
    audit_sink: SearchAuditSink,
) -> SearchPort:
    """Build exactly the explicitly selected provider without touching the inactive one."""
    backend = settings.get("TAP_SEARCH_BACKEND")
    if backend == "azure":
        return azure_factory(settings)
    if backend != "milvus":
        raise ValueError("TAP_SEARCH_BACKEND must be exactly 'azure' or 'milvus'")

    dimension_text = _required(settings, "TAP_MILVUS_DOC_VECTOR_DIMENSION")
    if _POSITIVE_INTEGER.fullmatch(dimension_text) is None:
        raise ValueError("TAP_MILVUS_DOC_VECTOR_DIMENSION must be a positive integer")
    dimension = int(dimension_text)
    if dimension > 4_096:
        raise ValueError("TAP_MILVUS_DOC_VECTOR_DIMENSION must not exceed 4096")
    target = MilvusIndexTarget(
        family=SourceFamily.DOC,
        alias=_required(settings, "TAP_MILVUS_DOC_ALIAS"),
        physical_name_prefix=_required(settings, "TAP_MILVUS_DOC_PHYSICAL_PREFIX"),
        schema_version=_required(settings, "TAP_MILVUS_DOC_SCHEMA_VERSION"),
        schema_sha256=_required(settings, "TAP_MILVUS_DOC_SCHEMA_SHA256"),
        corpus_version=_required(settings, "TAP_MILVUS_DOC_CORPUS_VERSION"),
        embedding_model_version=_required(settings, "TAP_MILVUS_DOC_EMBEDDING_MODEL"),
        vector_dimension=dimension,
    )
    config = MilvusSearchConfig(
        uri=_required(settings, "MILVUS_URI"),
        database=_required(settings, "MILVUS_DATABASE"),
        username=_required(settings, "MILVUS_READER_USERNAME"),
        password=SecretStr(_required(settings, "MILVUS_READER_PASSWORD")),
        targets={SourceFamily.DOC: target},
    )
    reader = milvus_reader_factory(config)
    return MilvusSearchAdapter(config, reader, audit_sink)


def _required(settings: Mapping[str, str], key: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required for the Milvus search backend")
    return value
