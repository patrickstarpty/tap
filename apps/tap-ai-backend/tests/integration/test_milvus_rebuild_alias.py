"""Persistence, rebuild, alias-switch, and scoped-reset real Milvus gates."""

from __future__ import annotations

import os

import pytest

RUN_REAL_MILVUS = os.getenv("TAP_RUN_MILVUS_INTEGRATION") == "1"
if not RUN_REAL_MILVUS:
    pytest.skip(
        "real Milvus suite is run by make test-milvus",
        allow_module_level=True,
    )

REQUIRED_ENV = (
    "MILVUS_URI",
    "MILVUS_DATABASE",
    "MILVUS_READER_USERNAME",
    "MILVUS_READER_PASSWORD",
    "MILVUS_WRITER_USERNAME",
    "MILVUS_WRITER_PASSWORD",
    "MILVUS_PROVISIONER_USERNAME",
    "MILVUS_PROVISIONER_PASSWORD",
)
missing = tuple(name for name in REQUIRED_ENV if not os.getenv(name))
if missing:
    pytest.fail(
        "missing required real Milvus settings: " + ", ".join(missing),
        pytrace=False,
    )

from milvus_runtime import PublishedFixture, scoped_volume_reset_command  # noqa: E402


@pytest.fixture
def published_fixture() -> PublishedFixture:
    return PublishedFixture.from_environment()


@pytest.mark.asyncio
async def test_real_milvus_manifest_digest_matches_before_and_after_restart(
    published_fixture: PublishedFixture,
) -> None:
    expected = published_fixture.expected_rebuild_digest()

    assert await published_fixture.live_rebuild_digest() == expected
    await published_fixture.restart_standalone()
    assert await published_fixture.live_rebuild_digest() == expected


@pytest.mark.asyncio
async def test_real_milvus_concurrent_alias_switch_never_mixes_physical_targets(
    published_fixture: PublishedFixture,
) -> None:
    observations = await published_fixture.run_alias_switch_race("refund-allowed")

    assert len(observations) >= 4
    assert all(len(observation.physical_collections) == 1 for observation in observations)
    assert all(observation.corpus_versions == {"corpus-fixture-v1"} for observation in observations)
    all_physical = set().union(*(observation.physical_collections for observation in observations))
    assert len(all_physical) == 2


def test_empty_rebuild_command_fails_closed_before_unscoped_volume_deletion() -> None:
    calls: list[tuple[str, ...]] = []

    with pytest.raises(ValueError, match="explicit volume reset opt-in"):
        scoped_volume_reset_command(
            "tap-milvus-local-experiment",
            allow_reset=False,
            recorder=calls.append,
        )
    with pytest.raises(ValueError, match="safe compose project"):
        scoped_volume_reset_command(
            "../shared",
            allow_reset=True,
            recorder=calls.append,
        )

    assert calls == []


def test_empty_rebuild_command_is_scoped_to_one_validated_compose_project() -> None:
    calls: list[tuple[str, ...]] = []
    project = os.getenv("TAP_MILVUS_COMPOSE_PROJECT", "tap-milvus-local-experiment")

    command = scoped_volume_reset_command(
        project,
        allow_reset=True,
        recorder=calls.append,
    )

    assert command == (
        "docker",
        "compose",
        "-p",
        project,
        "--profile",
        "milvus",
        "down",
        "-v",
        "--remove-orphans",
    )
    assert calls == [command]


@pytest.mark.asyncio
async def test_real_milvus_empty_rebuild_reconciles_exact_manifest_digest(
    published_fixture: PublishedFixture,
) -> None:
    assert await published_fixture.live_rebuild_digest() == (
        published_fixture.expected_rebuild_digest()
    )
