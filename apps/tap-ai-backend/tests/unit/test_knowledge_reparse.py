from __future__ import annotations

import pytest

from tap.entrypoints.knowledge_reparse import REPARSED_MEDIA_TYPES, cli, parse_arguments
from tap.modules.access.adapters.validation import VALIDATION_SCOPE


def test_reparse_help_documents_kept_revisions_and_the_degraded_window(capsys) -> None:
    """Operators must know old citations survive and documents briefly leave search."""
    with pytest.raises(SystemExit):
        cli(["knowledge", "reparse", "--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert "earlier revision and its artifacts are kept" in help_text
    assert "degraded window" in help_text
    assert "managed chunks" in help_text


def test_reparse_arguments_bind_the_validation_project_and_bound_the_limit() -> None:
    operation = parse_arguments(
        ["knowledge", "reparse", "--project", VALIDATION_SCOPE.project_id, "--limit", "8"]
    )

    assert operation.limit == 8
    assert REPARSED_MEDIA_TYPES == frozenset({"application/pdf"})
    for arguments in (
        ["knowledge", "reparse", "--project", "other"],
        ["knowledge", "reparse", "--project", VALIDATION_SCOPE.project_id, "--limit", "0"],
        ["knowledge", "reparse", "--project", VALIDATION_SCOPE.project_id, "--limit", "501"],
    ):
        with pytest.raises(SystemExit):
            parse_arguments(arguments)


def test_reparse_failure_is_reported_without_configuration_details(capsys) -> None:
    code = cli(["knowledge", "reparse", "--project", VALIDATION_SCOPE.project_id], environment={})

    assert code == 1
    assert capsys.readouterr().err.strip() == (
        "Knowledge reparse failed; verify local configuration and retry."
    )
