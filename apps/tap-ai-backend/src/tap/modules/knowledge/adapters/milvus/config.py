"""Immutable, secret-safe Milvus search target configuration."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit

from pydantic import SecretStr

from tap.modules.knowledge.domain.models import SourceFamily

_SAFE_MILVUS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}\Z")
_MAX_FILTER_BYTES = 32_768


@dataclass(frozen=True, slots=True)
class MilvusIndexTarget:
    family: SourceFamily
    alias: str
    physical_name_prefix: str
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int
    exact_generation_names: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.family, SourceFamily):
            raise TypeError("target family must be a closed source family")
        _safe_name("alias", self.alias)
        _safe_name("physical name prefix", self.physical_name_prefix)
        _bounded_string("schema version", self.schema_version)
        _canonical_sha256("schema SHA-256", self.schema_sha256)
        _bounded_string("corpus version", self.corpus_version)
        _bounded_string("embedding model version", self.embedding_model_version)
        if type(self.vector_dimension) is not int or self.vector_dimension < 1:
            raise ValueError("vector dimension must be a positive integer")
        if type(self.exact_generation_names) is not bool:
            raise TypeError("exact generation names must be a boolean")


def is_owned_physical_collection(
    base_name: str,
    candidate: object,
    *,
    exact_generation_names: bool,
) -> bool:
    """Apply the single configured physical-name authority for reader and writer."""

    if not isinstance(candidate, str) or _SAFE_MILVUS_NAME.fullmatch(candidate) is None:
        return False
    if exact_generation_names:
        generation_prefix = base_name + "_"
        suffix = candidate.removeprefix(generation_prefix)
        return candidate == base_name or (
            candidate.startswith(generation_prefix)
            and len(suffix) == 12
            and all(character in "0123456789abcdef" for character in suffix)
        )
    return candidate.startswith(base_name)


@dataclass(frozen=True, slots=True)
class MilvusSearchConfig:
    uri: str
    database: str
    username: str
    password: SecretStr = field(repr=False)
    targets: Mapping[SourceFamily, MilvusIndexTarget]
    candidate_limit: int = 50
    timeout_seconds: float = 8.0
    max_connections: int = 4
    max_filter_bytes: int = _MAX_FILTER_BYTES

    def __post_init__(self) -> None:
        _validate_uri(self.uri)
        _safe_name("database", self.database)
        _bounded_string("username", self.username)
        if not isinstance(self.password, SecretStr) or not self.password.get_secret_value():
            raise ValueError("password must be a non-empty secret")
        if not isinstance(self.targets, Mapping):
            raise TypeError("targets must be a mapping")
        copied_targets = dict(self.targets)
        target_keys = tuple(copied_targets)
        if len(target_keys) != 1 or target_keys[0] is not SourceFamily.DOC:
            raise ValueError("Milvus search requires exactly one doc target")
        target = copied_targets[SourceFamily.DOC]
        if not isinstance(target, MilvusIndexTarget) or target.family is not SourceFamily.DOC:
            raise ValueError("Milvus search requires exactly one matching doc target")
        object.__setattr__(self, "targets", MappingProxyType(copied_targets))
        _strict_int("candidate limit", self.candidate_limit, minimum=1, maximum=50)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 30
        ):
            raise ValueError("timeout seconds must be finite and greater than zero through 30")
        _strict_int("max connections", self.max_connections, minimum=1, maximum=16)
        _strict_int(
            "max filter bytes",
            self.max_filter_bytes,
            minimum=1,
            maximum=_MAX_FILTER_BYTES,
        )


def _validate_uri(uri: object) -> None:
    if not isinstance(uri, str) or not uri:
        raise ValueError("Milvus URI must be a non-empty URI")
    try:
        parsed = urlsplit(uri)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError("Milvus URI is malformed") from error
    if (
        parsed.scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Milvus URI must be an origin without embedded credentials")
    if parsed.scheme == "http" and hostname.lower() not in {"127.0.0.1", "localhost"}:
        raise ValueError("Milvus URI requires TLS unless it uses an exact loopback host")


def _safe_name(name: str, value: object) -> None:
    if not isinstance(value, str) or _SAFE_MILVUS_NAME.fullmatch(value) is None:
        raise ValueError(f"{name} must be a safe Milvus identifier")


def _bounded_string(name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{name} must be a bounded string")


def _canonical_sha256(name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be a canonical SHA-256 digest")


def _strict_int(name: str, value: object, *, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
