import pytest

from tap.modules.workflows.domain.dependencies import validate_dependency_dag


def test_rejects_indirect_dependency_cycle() -> None:
    edges = (("a", "b"), ("b", "c"))
    with pytest.raises(ValueError):
        validate_dependency_dag(edges, "c", "a")


def test_allows_independent_branch() -> None:
    validate_dependency_dag((("a", "b"), ("b", "c")), "a", "d")


def test_rejects_self_dependency() -> None:
    with pytest.raises(ValueError):
        validate_dependency_dag((), "a", "a")
