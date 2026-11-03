from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests import conftest


def _request(nodeid: str, scope: str = "function") -> SimpleNamespace:
    return SimpleNamespace(node=SimpleNamespace(nodeid=nodeid), scope=scope)


@pytest.fixture
def recorded_skips(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    skips: list[str] = []
    monkeypatch.setattr(conftest, "_MYSQL_SKIPS", skips)
    monkeypatch.delenv("TAP_RUN_MYSQL_INTEGRATION", raising=False)
    monkeypatch.delenv("TAP_REQUIRE_MYSQL_INTEGRATION", raising=False)
    return skips


def test_gate_skips_and_records_nodeid_without_mysql(recorded_skips: list[str]) -> None:
    with pytest.raises(pytest.skip.Exception, match="requires owned isolated MySQL"):
        conftest._mysql_gate(_request("tests/integration/test_x.py::test_a"))

    assert recorded_skips == ["tests/integration/test_x.py::test_a"]


def test_gate_fails_when_mysql_is_required(
    recorded_skips: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAP_REQUIRE_MYSQL_INTEGRATION", "1")

    with pytest.raises(
        pytest.fail.Exception,
        match=r"requires owned isolated MySQL \(TAP_REQUIRE_MYSQL_INTEGRATION=1\)",
    ):
        conftest._mysql_gate(_request("tests/integration/test_x.py::test_a"))

    assert recorded_skips == []


def test_gate_passes_when_mysql_integration_is_enabled(
    recorded_skips: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAP_RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.setenv("TAP_REQUIRE_MYSQL_INTEGRATION", "1")

    conftest._mysql_gate(_request("tests/integration/test_x.py::test_a"))

    assert recorded_skips == []


def test_skip_report_counts_each_test_once(recorded_skips: list[str]) -> None:
    nodeid = "tests/integration/test_x.py::test_a"
    with pytest.raises(pytest.skip.Exception):
        conftest._mysql_gate(_request(nodeid))
    for skipped_nodeid in (nodeid, "tests/integration/test_x.py::test_b"):
        conftest.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=skipped_nodeid,
                skipped=True,
                longrepr=("test_x.py", 1, "Skipped: requires owned isolated MySQL"),
            )
        )
    conftest.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="tests/unit/test_y.py::test_c",
            skipped=True,
            longrepr=("test_y.py", 1, "Skipped: unrelated"),
        )
    )

    assert recorded_skips == [nodeid, "tests/integration/test_x.py::test_b"]


def test_skip_summary_text() -> None:
    assert conftest._mysql_skip_summary(3) == (
        "MySQL persistence tests: 3 skipped (set TAP_RUN_MYSQL_INTEGRATION=1 with owned MySQL)"
    )


def test_terminal_summary_is_silent_without_mysql_skips(recorded_skips: list[str]) -> None:
    lines: list[str] = []
    reporter = SimpleNamespace(write_line=lines.append)

    conftest.pytest_terminal_summary(reporter)
    assert lines == []

    recorded_skips.extend(["a", "b"])
    conftest.pytest_terminal_summary(reporter)
    assert lines == [conftest._mysql_skip_summary(2)]
