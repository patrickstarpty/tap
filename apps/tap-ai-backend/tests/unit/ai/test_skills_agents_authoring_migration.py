"""Unit coverage for the 0035 downgrade retention guard.

Mirrors the fake-bind pattern used by
``tests/unit/test_management/test_test_design_review_migration.py``: a fake
``op.get_bind()`` whose ``execute`` reports whether the executed SQL text
contains a given "protected fact" substring, so each parametrized case
proves the guard's query actually inspects the right column/table rather
than an arbitrary row.
"""

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
        "ai_agent_revision WHERE description IS NOT NULL",
        "ai_agent_revision WHERE source_kind IS NOT NULL",
        "ai_agent_revision WHERE skill_revision_ids IS NOT NULL",
        "ai_agent_revision WHERE default_model_alias IS NOT NULL",
        "skill_revision WHERE description IS NOT NULL",
        "skill_revision WHERE source_kind IS NOT NULL",
        "skill_revision WHERE instruction_body IS NOT NULL",
        "skill_revision WHERE attached_reference_text IS NOT NULL",
        "skill_revision WHERE frontmatter_json IS NOT NULL",
        "skill_revision WHERE package_digest IS NOT NULL",
        "FROM skill_revision_file",
        "FROM skill_import",
    ],
)
def test_0035_downgrade_refuses_to_drop_authoring_facts(
    monkeypatch: pytest.MonkeyPatch, protected_fact: str
) -> None:
    migration = import_module("migrations.versions.0035_skills_agents_authoring")
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Bind(protected_fact))

    with pytest.raises(RuntimeError, match="require retention"):
        migration.downgrade()


def test_0035_downgrade_column_guard_ignores_an_unrelated_null_first_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A naive ``SELECT {column} FROM {table} LIMIT 1`` only inspects one
    arbitrary row: if that row happens to be NULL in the inspected column
    while a later row holds data, the guard must still refuse the
    downgrade. This is the regression the fix targets."""

    migration = import_module("migrations.versions.0035_skills_agents_authoring")

    class _NullFirstRowThenDataBind:
        """Simulates a table whose first row is NULL in every checked
        column but whose second row carries data in all of them — the
        exact shape a `LIMIT 1` read of an arbitrary row would miss."""

        def execute(self, statement):  # type: ignore[no-untyped-def]
            if "IS NOT NULL" in str(statement):
                # The fixed guard asks whether *any* row has data: yes.
                return _Result(True)
            # The old, buggy guard asked for an arbitrary row's value,
            # which (in this scenario) would have been NULL.
            return _Result(False)

    monkeypatch.setattr(migration.op, "get_bind", lambda: _NullFirstRowThenDataBind())
    with pytest.raises(RuntimeError, match="require retention"):
        migration.downgrade()
