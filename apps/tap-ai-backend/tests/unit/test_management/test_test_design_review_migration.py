from importlib import import_module

import pytest


class _Result:
    def __init__(self, found: bool) -> None:
        self._found = found

    def first(self):  # type: ignore[no-untyped-def]
        return (1,) if self._found else None


class _Bind:
    def __init__(self, protected_fact: str) -> None:
        self.protected_fact = protected_fact

    def execute(self, statement):  # type: ignore[no-untyped-def]
        return _Result(self.protected_fact in str(statement))


@pytest.mark.parametrize(
    "protected_fact",
    [
        "strict_review_required = 1",
        "claim_text IS NOT NULL",
        "requirement_ref IS NOT NULL",
    ],
)
def test_0022_downgrade_refuses_to_drop_governed_test_design_facts(
    monkeypatch, protected_fact: str
) -> None:
    migration = import_module("migrations.versions.0022_test_design_review")
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Bind(protected_fact))

    with pytest.raises(RuntimeError, match="requires retention"):
        migration.downgrade()
