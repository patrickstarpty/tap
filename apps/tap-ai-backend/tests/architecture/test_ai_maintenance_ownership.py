"""AI asset maintenance values and writes have one owner."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

AI_SOURCE = Path(__file__).resolve().parents[2] / "src/tap/modules/ai"
_REPORTING_MAINTENANCE = {
    "tap.modules.agent_team.adapters.mysql": {
        "agent_maintenance_event",
        "insert_maintenance_event",
    },
    "tap.modules.agent_team.domain.models": {
        "MaintenanceEvent",
        "MaintenanceKind",
        "new_event",
    },
    "tap.modules.skill_hub.adapters.mysql": {
        "skill_maintenance_event",
        "insert_skill_maintenance_event",
    },
    "tap.modules.skill_hub.domain.models": {
        "SkillMaintenanceEvent",
        "SkillMaintenanceKind",
        "new_skill_event",
    },
}


def _forbidden_maintenance_imports(source: str) -> set[tuple[str, str]]:
    forbidden: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module in _REPORTING_MAINTENANCE:
            forbidden.update(
                (node.module, alias.name)
                for alias in node.names
                if alias.name == "*" or alias.name in _REPORTING_MAINTENANCE[node.module]
            )
        elif isinstance(node, ast.Import):
            forbidden.update(
                (alias.name, "*") for alias in node.names if alias.name in _REPORTING_MAINTENANCE
            )
    return forbidden


@pytest.mark.parametrize(
    "source",
    [
        "from tap.modules.agent_team.adapters.mysql import insert_maintenance_event as write",
        "from tap.modules.agent_team.domain.models import MaintenanceEvent as Event",
        "from tap.modules.skill_hub.adapters.mysql import skill_maintenance_event",
        "from tap.modules.skill_hub.domain.models import new_skill_event",
        "import tap.modules.agent_team.adapters.mysql as persistence",
        "from tap.modules.skill_hub.domain.models import *",
    ],
)
def test_maintenance_boundary_rejects_reporting_owned_symbols(source: str) -> None:
    assert _forbidden_maintenance_imports(source)


def test_maintenance_boundary_allows_unrelated_reporting_reads() -> None:
    assert not _forbidden_maintenance_imports(
        "from tap.modules.agent_team.domain.models import Period\n"
        "from tap.modules.skill_hub.domain.models import CatalogItem"
    )


def test_ai_does_not_import_reporting_maintenance_ownership() -> None:
    violations = {
        str(path.relative_to(AI_SOURCE)): _forbidden_maintenance_imports(path.read_text())
        for path in AI_SOURCE.rglob("*.py")
        if _forbidden_maintenance_imports(path.read_text())
    }
    assert not violations


def test_maintenance_tables_share_platform_metadata_and_ai_owner() -> None:
    from tap.modules.ai.adapters.maintenance import (
        agent_maintenance_event,
        insert_maintenance_event,
        insert_skill_maintenance_event,
        skill_maintenance_event,
    )
    from tap.modules.ai.domain.maintenance import MaintenanceEvent, SkillMaintenanceEvent
    from tap.platform.db.schema import metadata

    assert metadata.tables["agent_maintenance_event"] is agent_maintenance_event
    assert metadata.tables["skill_maintenance_event"] is skill_maintenance_event
    assert MaintenanceEvent.__module__ == "tap.modules.ai.domain.maintenance"
    assert SkillMaintenanceEvent.__module__ == "tap.modules.ai.domain.maintenance"
    assert insert_maintenance_event.__module__ == "tap.modules.ai.adapters.maintenance"
    assert insert_skill_maintenance_event.__module__ == "tap.modules.ai.adapters.maintenance"
