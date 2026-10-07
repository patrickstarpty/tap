from __future__ import annotations

import pytest

from tap.entrypoints.graph_operator import parse_arguments
from tap.modules.access.adapters.validation import VALIDATION_SCOPE


def test_graph_rebuild_arguments_bind_the_validation_project() -> None:
    operation = parse_arguments(
        ["graph", "rebuild", "--project", VALIDATION_SCOPE.project_id, "--limit", "5"]
    )

    assert operation.command == "rebuild"
    assert operation.limit == 5
    assert operation.interval_seconds == 1.0

    with pytest.raises(SystemExit):
        parse_arguments(["graph", "rebuild", "--project", "other"])
