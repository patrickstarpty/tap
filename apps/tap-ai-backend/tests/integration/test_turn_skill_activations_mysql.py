"""Skill Hub phase 1 (Task 6): `skill.activate` rows in `turn_skill_activation`
against owned MySQL -- idempotent on a resumed `tools` node, and merged with
the frozen manual/pinned rows for the Turn's "Skills used" view."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_chat_turn import ChatTurnNodes
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
from tap.modules.ai.domain.assets import SkillRevision, text_digest
from tap.modules.ai.domain.skill_activation import SkillCatalogEntry
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.adapters.mysql_skill_activations import (
    MysqlSkillActivationRuntime,
    MysqlTurnSkillActivations,
    activation_id_for,
)
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.application.ports import TurnSkillUse
from tap.modules.chat.domain.conversations import TurnInput
from tests.owned_mysql import owned_project_database_url


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sessions(owned_project_mysql):
    url = owned_project_database_url(owned_project_mysql).replace("mysql+pymysql", "mysql+asyncmy")
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _insert_skill(
    catalog: MysqlAssetCatalog, skill_id: str, name: str, body: str, *, revision: int = 1
) -> None:
    await catalog.insert_skill_revision(
        SkillRevision(
            revision_id=f"{skill_id}-v{revision}",
            asset_id=skill_id,
            display_name=name,
            scope=VALIDATION_SCOPE,
            content_digest=text_digest(f"{skill_id}-content-{revision}"),
            instruction_template_digest=text_digest(body),
            applicable_tasks=frozenset({"knowledge.answer"}),
            instruction_template=body,
        ),
        base_revision_id=None if revision == 1 else f"{skill_id}-v{revision - 1}",
    )


def _entry(skill_id: str, name: str) -> SkillCatalogEntry:
    return SkillCatalogEntry(
        skill_id=skill_id,
        revision_id=f"{skill_id}-v1",
        name=name,
        description=f"{name} description",
        category="other",
    )


class _Conversations:
    def __init__(self) -> None:
        self.scope = VALIDATION_SCOPE
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, conversation_id, turn_id, event_type, payload, *, lease_token=None):
        self.emitted.append((event_type, payload))


async def _no_auth() -> None:
    return None


def test_resumed_tools_node_does_not_duplicate_activation_row(owned_project_mysql):
    body = "Check every release gate. 数据"

    async def scenario():
        engine, sessions = _sessions(owned_project_mysql)
        try:
            catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
            await _insert_skill(catalog, "auto-checklist", "release-checklist", body)
            value = TurnInput(
                message="Prepare the release",
                actor_id=VALIDATION_SCOPE.actor_id,
                identity_mode="validation",
                model_alias="qwen-plus",
                skill_catalog=(_entry("auto-checklist", "release-checklist"),),
            )
            turn = SimpleNamespace(
                turn_id="auto-turn",
                lease_token="lease-auto",
                attempt=1,
                input_snapshot=SimpleNamespace(snapshot_id="snapshot-auto", value=value),
            )
            conversations = _Conversations()
            nodes = ChatTurnNodes(
                conversation_id="auto-conversation",
                turn=turn,
                conversations=conversations,
                steps=object(),  # type: ignore[arg-type]
                planner=None,
                knowledge=object(),
                insights_explanation=None,
                require_authorized=_no_auth,
                skills=MysqlSkillActivationRuntime(catalog, sessions, scope=VALIDATION_SCOPE),
            )
            state = {
                "tools": ["knowledge.search", "skill.activate"],
                "budget": {"maxToolRounds": 6},
                "budget_used": {"modelCalls": 0, "costMicros": 0, "toolRounds": 0},
                "step": {
                    "kind": "tool_calls",
                    "bundle": {},
                    "transcript": [],
                    "pending_tool_calls": [
                        {
                            "callId": "call-1",
                            "name": "skill_activate",
                            "argumentsJson": json.dumps({"name": "release-checklist"}),
                        }
                    ],
                },
            }

            # A worker that crashed after the `tools` node ran but before its
            # checkpoint was written re-runs the node from the same state.
            first = await nodes.tools(state)
            resumed = await nodes.tools(state)

            for result in (first, resumed):
                reply = json.loads(result["step"]["transcript"][-1]["content"])
                assert reply == {"status": "activated", "name": "release-checklist"}
            async with engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            text(
                                "SELECT activation_id, project_id, skill_id, skill_revision_id, "
                                "mode, bytes FROM turn_skill_activation "
                                "WHERE turn_id='auto-turn'"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            assert [dict(row) for row in rows] == [
                {
                    "activation_id": activation_id_for("auto-turn", "auto-checklist"),
                    "project_id": VALIDATION_SCOPE.project_id,
                    "skill_id": "auto-checklist",
                    "skill_revision_id": "auto-checklist-v1",
                    "mode": "auto",
                    "bytes": len(body.encode("utf-8")),
                }
            ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_for_turns_merges_frozen_and_auto_rows(owned_project_mysql):
    manual_text = "Manual instruction"

    async def scenario():
        engine, sessions = _sessions(owned_project_mysql)
        try:
            catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
            await _insert_skill(catalog, "merge-manual", "Merge Manual", manual_text)
            # Renamed before the Turn: the Skill row keeps its creation-time
            # name, while the Turn activates the renamed `merge-auto-v2`.
            await _insert_skill(catalog, "merge-auto", "Original Auto", "Old instruction")
            await _insert_skill(catalog, "merge-auto", "Merge Auto", "Auto instruction", revision=2)
            service = ConversationService(
                MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
                scope=VALIDATION_SCOPE,
            )
            await service.create(
                "merge-conversation",
                "merge-turn",
                "merge-request",
                TurnInput(
                    message="Use Skills",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    identity_mode="validation",
                    model_alias="qwen-plus",
                    skill_revision_ids=("merge-manual-v1",),
                    skill_revision_digests=("sha256:" + "b" * 64,),
                    skill_instruction_templates=(manual_text,),
                    skill_instruction_template_digests=(_sha(manual_text),),
                    requested_skill_revision_ids=("merge-manual-v1",),
                    skill_catalog=(_entry("merge-auto", "Merge Auto"),),
                    retrieval_policy_digest="sha256:" + "c" * 64,
                ),
            )
            runtime = MysqlSkillActivationRuntime(catalog, sessions, scope=VALIDATION_SCOPE)
            await runtime.record_auto(
                turn_id="merge-turn",
                skill_id="merge-auto",
                skill_revision_id="merge-auto-v2",
                bytes_=16,
                activated_at=datetime.now(UTC) + timedelta(seconds=5),
            )
            # Both Skills are renamed after the Turn: the label stays the
            # name of the revision the Turn used, matching `skillLabels`.
            await _insert_skill(catalog, "merge-manual", "Renamed Manual", "New", revision=2)
            await _insert_skill(catalog, "merge-auto", "Renamed Auto", "New", revision=3)

            uses = await MysqlTurnSkillActivations(sessions, scope=VALIDATION_SCOPE).for_turns(
                ("merge-turn", "unknown-turn")
            )

            assert uses == {
                "merge-turn": (
                    TurnSkillUse("merge-manual", "merge-manual-v1", "Merge Manual", "manual"),
                    TurnSkillUse("merge-auto", "merge-auto-v2", "Merge Auto", "auto"),
                )
            }
            assert (
                await MysqlTurnSkillActivations(sessions, scope=VALIDATION_SCOPE).for_turns(())
                == {}
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
