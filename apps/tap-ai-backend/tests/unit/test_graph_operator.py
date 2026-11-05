from __future__ import annotations

import pytest

from tap.entrypoints.graph_operator import cli, parse_arguments
from tap.modules.access.adapters.validation import VALIDATION_SCOPE


def test_rebuild_help_documents_the_degraded_window(capsys) -> None:
    """`graph rebuild` no longer requests a merge itself (fragments request
    their own as they republish); the help text must say so, and must warn
    that the graph shrinks to the re-extracted subset once the first
    fragment republishes and grows back as the rest finish, with the
    previous version kept for one cycle but not served by query routes."""
    with pytest.raises(SystemExit):
        cli(["graph", "rebuild", "--help"])

    help_text = capsys.readouterr().out
    assert "does not request a merge" in help_text
    assert "degraded" in help_text
    assert "previous" in help_text and "version" in help_text


def test_graph_rebuild_arguments_bind_the_validation_project() -> None:
    operation = parse_arguments(
        ["graph", "rebuild", "--project", VALIDATION_SCOPE.project_id, "--limit", "5"]
    )

    assert operation.command == "rebuild"
    assert operation.limit == 5
    assert operation.interval_seconds == 1.0

    with pytest.raises(SystemExit):
        parse_arguments(["graph", "rebuild", "--project", "other"])
