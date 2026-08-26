from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

_RUNTIME_PATH = Path(__file__).resolve().parents[2] / "integration" / "milvus_runtime.py"
_RUNTIME_SPEC = importlib.util.spec_from_file_location(
    "milvus_runtime_rebuild_test_module",
    _RUNTIME_PATH,
)
assert _RUNTIME_SPEC is not None and _RUNTIME_SPEC.loader is not None
runtime = importlib.util.module_from_spec(_RUNTIME_SPEC)
sys.modules[_RUNTIME_SPEC.name] = runtime
_RUNTIME_SPEC.loader.exec_module(runtime)


def _published_fixture() -> object:
    manifest = runtime.load_doc_fixture(runtime._DOC_FIXTURE)
    cases = runtime.load_query_cases(runtime._QUERY_FIXTURE)
    snapshot = runtime._load_snapshot(runtime._VECTOR_SNAPSHOT, manifest, cases)
    settings = runtime.MilvusRuntimeSettings(
        uri="http://127.0.0.1:1",
        database="unused",
        reader_username="unused",
        reader_password=SecretStr("unused"),
        writer_username="unused",
        writer_password=SecretStr("unused"),
        provisioner_username="unused",
        provisioner_password=SecretStr("unused"),
        compose_project="unused",
    )
    return runtime.PublishedFixture(settings, manifest, cases, snapshot)


def test_expected_rebuild_digest_uses_the_closed_canonical_field_contract() -> None:
    fixture = _published_fixture()

    assert (
        fixture.expected_rebuild_digest()
        == "sha256:847d7bf727060e3c71180dd9524dec99d652ba7422f794fa05ba09c078d43dd4"
    )


def test_rebuild_rows_reject_fields_outside_each_closed_contract() -> None:
    fixture = _published_fixture()
    rows = runtime.fixture_rows(
        fixture.manifest,
        {item_id: record.vector for item_id, record in fixture.snapshot.chunks.items()},
    )
    canonical = {name: rows[0][name] for name in runtime._REBUILD_FIELDS}

    with pytest.raises(AssertionError, match="widened or incomplete"):
        runtime._rebuild_row({**canonical, "unexpected_field": None})
    with pytest.raises(AssertionError, match="widened or incomplete"):
        runtime._expected_rebuild_row({**rows[0], "unexpected_field": None})
